"""Answer the invoices employees send to a Google Chat direct message.

One run reads the signed-in account's direct messages that moved since the
last run, takes each new message carrying an image or a PDF from a person the
tenant's team.yaml names, has Claude judge it against the tenant's rubric,
checks it against the ledger for a duplicate and against the week's allowance,
replies, tasks a person with the payment, and records it in the ledger.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

import majordomo
from majordomo import dates

HERE = Path(__file__).resolve().parent
LEDGER_FIELDS = ["handled_at", "employee", "vendor", "invoice_date", "amount", "message", "outcome"]
PAID = ("within", "overdrawn")
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
SEND_MODES = ("off", "test", "live")


@dataclass
class Tenant:
    root: Path
    people: dict[str, str]          # users/<id> -> name, from .reviewagent/team.yaml
    tz: ZoneInfo
    task_space: str
    week_starts: int                # 0 = Monday
    overdraft_limit: float
    allowances: dict[str, float]    # name -> weekly allowance

    @property
    def folder(self) -> Path:
        return self.root / ".reimbursement"

    @classmethod
    def load(cls, root: Path) -> "Tenant":
        team = yaml.safe_load((root / ".reviewagent" / "team.yaml").read_text())
        tenant = yaml.safe_load((root / ".reviewagent" / "settings.yaml").read_text())
        own = yaml.safe_load((root / ".reimbursement" / "settings.yaml").read_text())
        return cls(root=root, people={uid: name for name, uid in team["people"].items()},
                   tz=ZoneInfo(tenant["timezone"]), task_space=own["task_space"],
                   week_starts=WEEKDAYS.index(own["week_starts"].lower()),
                   overdraft_limit=float(own["overdraft_limit"]),
                   allowances={k: float(v) for k, v in own["allowances"].items()})

    def week_start(self, when: datetime) -> datetime:
        """The start of the allowance week holding ``when``, on the tenant's clock."""
        local = when.astimezone(self.tz)
        day = local.date() - timedelta(days=(local.weekday() - self.week_starts) % 7)
        return datetime(day.year, day.month, day.day, tzinfo=self.tz)


class Ledger:
    """One line per invoice message handled, in a tab-separated file."""

    def __init__(self, path: Path, others: tuple[Path, ...] = ()):
        self.path = path
        self.rows = [r for p in (path, *others) if p.exists()
                     for r in csv.DictReader(p.open(newline=""), delimiter="\t")]

    def has(self, message: str) -> bool:
        return any(r["message"] == message for r in self.rows)

    def duplicate_of(self, invoice: dict) -> dict | None:
        """An earlier paid line with the same invoice date, and the same vendor or amount."""
        for r in self.rows:
            if r["outcome"] not in PAID or not invoice.get("invoice_date"):
                continue
            if r["invoice_date"] == invoice["invoice_date"] and (
                    r["vendor"].strip().lower() == (invoice.get("vendor") or "").strip().lower()
                    or float(r["amount"]) == float(invoice["amount"])):
                return r
        return None

    def spent(self, employee: str, since: datetime) -> float:
        return sum(float(r["amount"]) for r in self.rows
                   if r["employee"] == employee and r["outcome"] in PAID and dates.utc(r["handled_at"]) >= since)

    def add(self, row: dict) -> None:
        new = not self.path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", newline="") as fh:
            out = csv.DictWriter(fh, LEDGER_FIELDS, delimiter="\t")
            if new:
                out.writeheader()
            out.writerow(row)
        self.rows.append(row)


def judge_with_claude(invoice: Path, text: str | None, rubric: Path) -> dict:
    """The skill's answer for one invoice, through `claude -p` run from this directory."""
    prompt = (f"Use the reimbursement skill. Invoice file: {invoice}. Rubric: {rubric}. "
              f"The employee's message: {text or '(none)'}")
    done = subprocess.run(
        ["claude", "-p", prompt, "--output-format", "json", "--allowedTools", "Read",
         "--add-dir", str(invoice.parent), "--add-dir", str(rubric.parent)],
        cwd=HERE, capture_output=True, text=True, timeout=300, check=True)
    result = json.loads(done.stdout)["result"]
    found = re.search(r"\{.*\}", result, re.S)
    if not found:
        raise ValueError(f"no JSON object in the judgement: {result[:200]!r}")
    answer = json.loads(found[0])
    missing = {"vendor", "invoice_date", "amount", "currency", "qualified", "reason", "hint"} - answer.keys()
    if missing:
        raise ValueError(f"the judgement lacks {sorted(missing)}")
    return answer


def decide(invoice: dict, employee: str, tenant: Tenant, ledger: Ledger, now: datetime) -> tuple[str, dict | None, float]:
    """The outcome for one judged invoice, the earlier line it duplicates, and the week's allowance left after it."""
    if not invoice["qualified"]:
        return "rejected", None, 0.0
    earlier = ledger.duplicate_of(invoice)
    if earlier:
        return "duplicate", earlier, 0.0
    left = tenant.allowances.get(employee, 0.0) - ledger.spent(employee, tenant.week_start(now)) - float(invoice["amount"])
    if left >= 0:
        return "within", None, left
    if left >= -tenant.overdraft_limit:
        return "overdrawn", None, left
    return "over-limit", None, left


def money(x: float) -> str:
    return f"-${-x:,.2f}" if x < 0 else f"${x:,.2f}"


def reply_text(outcome: str, invoice: dict, earlier: dict | None, left: float) -> str:
    what = f"{invoice.get('vendor')}, {invoice.get('invoice_date') or 'undated'}, {money(float(invoice.get('amount') or 0))}"
    if outcome == "rejected":
        return f"This invoice ({what}) does not qualify for reimbursement: {invoice['reason']} {invoice.get('hint') or ''}".strip()
    if outcome == "duplicate":
        return (f"This invoice ({what}) looks like one already reimbursed: {earlier['vendor']}, "
                f"{earlier['invoice_date']}, {money(float(earlier['amount']))}. If it is a different purchase, reply and say so.")
    if outcome == "within":
        return f"Received ({what}). It will be reimbursed. This week's allowance left: {money(left)}."
    if outcome == "overdrawn":
        return f"Received ({what}). It will be reimbursed, and this week's allowance is overdrawn by {money(-left)}."
    return (f"Received ({what}). It is beyond this week's allowance and overdraft by {money(-left)}, "
            "so it has gone to Accounts to decide.")


def task_text(outcome: str, employee: str, invoice: dict, left: float) -> str:
    head = (f"Reimburse {employee}: {invoice['vendor']}, {invoice.get('invoice_date') or 'undated'}, "
            f"{money(float(invoice['amount']))} {invoice.get('currency') or ''}".rstrip())
    if outcome == "within":
        return f"{head}. Within the week's allowance."
    if outcome == "overdrawn":
        return f"{head}. Overdraws the week's allowance by {money(-left)}, within the overdraft limit."
    return f"{head}. Not reimbursed yet: beyond the week's allowance and overdraft by {money(-left)}. Decide whether to pay."


class Run:
    def __init__(self, conf: dict, *, judge=judge_with_claude, chat=majordomo, git=subprocess.run, now=None):
        self.send_mode = conf.get("send", "off")
        if self.send_mode not in SEND_MODES:
            raise SystemExit(f"send must be one of {', '.join(SEND_MODES)}, not {self.send_mode!r}")
        self.test_to = conf.get("test_correspondent")
        if self.send_mode == "test" and not self.test_to:
            raise SystemExit("send = \"test\" needs test_correspondent")
        self.state = Path(conf["state_dir"]).expanduser()
        self.tenant = Tenant.load(Path(conf["tenant"]).expanduser())
        self.chat, self.judge, self.git = chat, judge, git
        self.cfg = chat.load_config(str(Path(conf["majordomo_config"]).expanduser()))
        self.me = (self.cfg.get("me") or {}).get("user_id")
        if not self.me:
            raise SystemExit("the majordomo configuration names no [me] user_id, so the agent cannot tell its own messages")
        live = self.tenant.folder / "ledger.tsv"
        test = self.state / "ledger-test.tsv"
        self.ledger = Ledger(live, (test,)) if self.send_mode == "live" else Ledger(test, (live,))
        self.now = now or datetime.now(timezone.utc)
        self.out: list[str] = []
        self.failed = False

    @property
    def mark_file(self) -> Path:
        return self.state / "mark.json"

    def mark(self) -> datetime:
        if self.mark_file.exists():
            return dates.utc(json.loads(self.mark_file.read_text())["mark"])
        return self.now - timedelta(days=1)

    def accepts(self, sender: str | None) -> bool:
        if sender is None or sender == self.me:
            return False
        if self.send_mode == "test":
            return sender == self.test_to
        return sender in self.tenant.people

    def say(self, *, to: str | None = None, space: str | None = None, text: str, files=()) -> None:
        self.out.append(f"[{to or space}] {text}" + (f" (+{len(files)} file)" if files else ""))
        if self.send_mode == "off":
            return
        if self.send_mode == "test":
            to, space = self.test_to, None
        self.chat.send(self.cfg, to=to, space=space, text=text, attachments=list(files) or None,
                       cache_dir=str(self.state / "cache"))

    def record(self, row: dict) -> None:
        self.ledger.add(row)
        if self.send_mode != "live":
            return
        msg = (f"{row['employee']}: {row['vendor']}, {row['invoice_date']}, {money(float(row['amount']))}, {row['outcome']}"
               f"\n\nChat message {row['message']}")
        root = str(self.tenant.root)
        self.git(["git", "-C", root, "add", str(self.ledger.path)], check=True)
        self.git(["git", "-C", root, "commit", "-q", "-m", msg, "--", str(self.ledger.path)], check=True)

    def handle(self, row: dict, text: str | None) -> None:
        sender = row["sender_name"]
        employee = self.tenant.people.get(sender, sender)
        path = Path(row["path"])
        try:
            invoice = self.judge(path, text, self.tenant.folder / "rubric.md")
        except Exception as exc:  # the mark stays put, so the next run reads this message again
            print(f"reimburse: {row['message_name']}: judgement failed: {exc}", file=sys.stderr)
            self.failed = True
            return
        outcome, earlier, left = decide(invoice, employee, self.tenant, self.ledger, self.now)
        self.say(to=sender, text=reply_text(outcome, invoice, earlier, left))
        if outcome in ("within", "overdrawn", "over-limit"):
            self.say(space=self.tenant.task_space, text=task_text(outcome, employee, invoice, left), files=[path])
        self.record({"handled_at": self.now.isoformat(timespec="seconds"), "employee": employee,
                     "vendor": invoice.get("vendor") or "", "invoice_date": invoice.get("invoice_date") or "",
                     "amount": invoice.get("amount") or 0, "message": row["message_name"], "outcome": outcome})

    def run(self) -> list[str]:
        since = self.mark()
        work = self.state / "work"
        cache = str(self.state / "cache")
        reader = self.chat.make_reader(self.cfg, "nocache", cache_dir=cache)
        for sp in reader.spaces():
            last = sp.get("last_active")
            if sp.get("space_type") != "DIRECT_MESSAGE" or last is None or dates.utc(last) <= since:
                continue
            files = [r for r in self.chat.attachments(self.cfg, space=sp["space_name"], start=since, cache_dir=cache)
                     if (r.get("content_type") or "").startswith("image/") or r.get("content_type") == "application/pdf"]
            files = [r for r in files if self.accepts(r.get("sender_name")) and not self.ledger.has(r["message_name"])]
            if not files:
                continue
            texts = {m["name"]: m.get("text") for m in self.chat.messages(self.cfg, sp["space_name"], start=since,
                                                                          source="nocache", cache_dir=cache)}
            for r in files:
                dest = work / r["message_name"].replace("/", "_")
                dest.mkdir(parents=True, exist_ok=True)
                (fetched,) = self.chat.attachments(self.cfg, message=r["message_name"], download_to=str(dest),
                                                   cache_dir=cache)[:1] or [{}]
                if not fetched.get("path"):
                    print(f"reimburse: {r['message_name']}: {fetched.get('error', 'not saved')}", file=sys.stderr)
                    self.failed = True
                    continue
                self.handle(fetched, texts.get(r["message_name"]))
        if not self.failed:
            self.state.mkdir(parents=True, exist_ok=True)
            self.mark_file.write_text(json.dumps({"mark": self.now.isoformat()}))
        return self.out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="reimburse", description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True, help="the agent's TOML configuration file")
    args = parser.parse_args(argv)
    with open(args.config, "rb") as fh:
        conf = tomllib.load(fh)
    for line in Run(conf).run():
        print(line)


if __name__ == "__main__":
    main()

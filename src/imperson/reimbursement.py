"""The reimbursement skill's tools: the figures behind an employee's invoice, served in-process.

The model reads the invoice and judges it against the tenant's rubric; these tools
hold the allowance, the ledger and the payment task, so the numbers never come
from the model.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from claude_agent_sdk import tool

LEDGER_FIELDS = ["handled_at", "employee", "vendor", "invoice_date", "amount", "message", "outcome"]
PAID = ("within", "overdrawn")
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


@dataclass
class Tenant:
    root: Path
    people: dict[str, str]          # users/<id> -> name, from .reviewagent/team.yaml
    tz: ZoneInfo
    task_space: str
    week_starts: int                # 0 = Monday
    overdraft_limit: float
    allowances: dict[str, float]    # name or Chat id -> weekly allowance; anyone else is expected to give a reason

    @property
    def folder(self) -> Path:
        return self.root / ".imperson" / "reimbursement"

    @classmethod
    def load(cls, root: Path) -> "Tenant":
        team = yaml.safe_load((root / ".reviewagent" / "team.yaml").read_text())
        tenant = yaml.safe_load((root / ".reviewagent" / "settings.yaml").read_text())
        own = yaml.safe_load((root / ".imperson" / "reimbursement" / "settings.yaml").read_text())
        return cls(root=root, people={uid: name for name, uid in team["people"].items()},
                   tz=ZoneInfo(tenant["timezone"]), task_space=own["task_space"],
                   week_starts=WEEKDAYS.index(own["week_starts"].lower()),
                   overdraft_limit=float(own["overdraft_limit"]),
                   allowances={k: float(v) for k, v in (own.get("allowances") or {}).items()})

    def week_start(self, when: datetime) -> datetime:
        """The start of the allowance week holding ``when``, on the tenant's clock."""
        local = when.astimezone(self.tz)
        day = local.date() - timedelta(days=(local.weekday() - self.week_starts) % 7)
        return datetime(day.year, day.month, day.day, tzinfo=self.tz)


class Ledger:
    """One line per invoice handled, in a tab-separated file; a message may carry several invoices."""

    def __init__(self, path: Path, others: tuple[Path, ...] = ()):
        self.path = path
        self.rows = [r for p in (path, *others) if p.exists()
                     for r in csv.DictReader(p.open(newline=""), delimiter="\t")]

    def has(self, message: str, vendor: str, invoice_date: str, amount: float) -> bool:
        return any(r["message"] == message and r["vendor"].strip().lower() == vendor.strip().lower()
                   and r["invoice_date"] == invoice_date and float(r["amount"]) == amount for r in self.rows)

    def duplicate_of(self, invoice_date: str, vendor: str, amount: float, message: str) -> dict | None:
        """A line from another message with the same invoice date, and the same vendor or amount; the invoices
        of one message are distinct even when they share a shop and a day."""
        for r in self.rows:
            if r["outcome"] != "not-justifiable" and r["message"] != message and r["invoice_date"] == invoice_date and (
                    r["vendor"].strip().lower() == vendor.strip().lower() or float(r["amount"]) == amount):
                return r
        return None

    def spent(self, employee: str, since: datetime) -> float:
        return sum(float(r["amount"]) for r in self.rows
                   if r["employee"] == employee and r["outcome"] in PAID and when(r["handled_at"]) >= since)

    def add(self, row: dict) -> None:
        new = not self.path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", newline="") as fh:
            out = csv.DictWriter(fh, LEDGER_FIELDS, delimiter="\t")
            if new:
                out.writeheader()
            out.writerow(row)
        self.rows.append(row)


def when(stamp: str) -> datetime:
    parsed = datetime.fromisoformat(stamp)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def money(x: float) -> str:
    return f"-${-x:,.2f}" if x < 0 else f"${x:,.2f}"


def reply(text: str, error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], **({"is_error": True} if error else {})}


def tools(turn) -> list:
    tenant = Tenant.load(turn.tenant)
    live = tenant.folder / "ledger.tsv"
    test = turn.state / "reimbursement-ledger-test.tsv"
    ledger = Ledger(live, (test,)) if turn.send == "live" else Ledger(test, (live,))
    person = tenant.people.get(turn.sender) or turn.sender   # the team's name for them, else their Chat id
    since = tenant.week_start(turn.now)

    allowance = tenant.allowances.get(person, tenant.allowances.get(turn.sender))

    def standing() -> tuple[float, float]:
        spent = ledger.spent(person, since)
        return spent, allowance - spent

    def record(row: dict) -> None:
        ledger.add(row)
        if turn.send != "live":
            return
        msg = (f"{row['employee']}: {row['vendor']}, {row['invoice_date']}, {money(float(row['amount']))}, "
               f"{row['outcome']}\n\nChat message {row['message']}")
        turn.git(["-C", str(tenant.root), "add", str(live)])
        turn.git(["-C", str(tenant.root), "commit", "-q", "-m", msg, "--", str(live)])

    def row_for(vendor: str, invoice_date: str, amount: float, outcome: str) -> dict:
        return {"handled_at": turn.now.isoformat(timespec="seconds"), "employee": person,
                "vendor": vendor, "invoice_date": invoice_date, "amount": amount,
                "message": turn.message, "outcome": outcome}

    @tool("allowance_left",
          "Whether the sender has a weekly allowance, and if so what they have spent this week and what is left, with "
          "the week's start date and the overdraft limit. A person with an allowance is not expected to give a reason "
          "for a justifiable expense; a person without one is. Call it before handling an invoice.",
          {"type": "object", "properties": {}})
    async def allowance_left(args):
        if allowance is None:
            return reply(f"{person} has no allowance: a reason is expected for each expense, and the payment task states it.")
        spent, left = standing()
        return reply(f"{person}: weekly allowance {money(allowance)}, spent this week {money(spent)}, "
                     f"left {money(left)}. The week started {since.date().isoformat()}. "
                     f"Overdraft limit {money(tenant.overdraft_limit)}. No reason is expected for a justifiable expense.")

    @tool("find_duplicate",
          "The earlier reimbursed invoice that an invoice duplicates (same date, and the same vendor or the "
          "same amount), or that there is none. Call it once the vendor, date and amount are read off the invoice.",
          {"type": "object", "properties": {"invoice_date": {"type": "string", "description": "YYYY-MM-DD"},
                                            "vendor": {"type": "string"}, "amount": {"type": "number"}},
           "required": ["invoice_date", "vendor", "amount"]})
    async def find_duplicate(args):
        earlier = ledger.duplicate_of(args["invoice_date"], args["vendor"], float(args["amount"]), turn.message)
        if not earlier:
            return reply("No earlier reimbursed invoice duplicates this one.")
        return reply(f"Duplicate of an earlier line: {earlier['employee']}, {earlier['vendor']}, "
                     f"{earlier['invoice_date']}, {money(float(earlier['amount']))}, {earlier['outcome']}, "
                     f"handled {earlier['handled_at']}.")

    @tool("record_reimbursement",
          "Record one justifiable expense from this message, once for each when the message holds several, and post a "
          "task for a person to reimburse it, with the invoice attached, to the business's task space. For a person with "
          "an allowance the task states whether it is within the allowance, within the overdraft or above it; for a "
          "person without one, pass the reason they gave, which the task states.",
          {"type": "object", "properties": {"vendor": {"type": "string"}, "invoice_date": {"type": "string", "description": "YYYY-MM-DD"},
                                            "amount": {"type": "number"}, "currency": {"type": "string"},
                                            "file": {"type": "string", "description": "Path of the invoice, one of this message's files"},
                                            "reason": {"type": "string", "description": "What the person said it was for"}},
           "required": ["vendor", "invoice_date", "amount", "currency", "file"]})
    async def record_reimbursement(args):
        vendor, invoice_date, amount = args["vendor"], args["invoice_date"], float(args["amount"])
        if ledger.has(turn.message, vendor, invoice_date, amount):
            return reply("This invoice of this message is already recorded; it is not recorded twice.", True)
        file = Path(args["file"]).resolve()
        if file not in [Path(f).resolve() for f in turn.files]:
            return reply("That file is not one saved from this message.", True)
        earlier = ledger.duplicate_of(invoice_date, vendor, amount, turn.message)
        if earlier:
            return reply(f"Refused: a duplicate of an earlier line: {earlier['employee']}, {earlier['vendor']}, "
                         f"{earlier['invoice_date']}, {money(float(earlier['amount']))}.", True)
        head = f"Reimburse {person}: {vendor}, {invoice_date}, {money(amount)} {args['currency']}"
        if allowance is None:
            if not args.get("reason"):
                return reply(f"{person} has no allowance, so a reason is expected: ask them what it was for.", True)
            record(row_for(vendor, invoice_date, amount, "reason-given"))
            turn.post(tenant.task_space, f"{head}. Reason given: {args['reason']}", [str(file)])
            return reply("Recorded, and a task to reimburse it, stating the reason, has been posted.")
        left = standing()[1] - amount
        outcome = "within" if left >= 0 else "overdrawn" if left >= -tenant.overdraft_limit else "over-limit"
        record(row_for(vendor, invoice_date, amount, outcome))
        if outcome == "within":
            task = f"{head}. Within the week's allowance."
        elif outcome == "overdrawn":
            task = f"{head}. Overdraws the week's allowance by {money(-left)}, within the overdraft limit."
        else:
            task = f"{head}. Not reimbursed yet: beyond the week's allowance and overdraft by {money(-left)}. Decide whether to pay."
        turn.post(tenant.task_space, task, [str(file)])
        return reply(f"Outcome: {outcome}. Allowance left after this invoice: {money(left)}."
                     + (" The payment task asks the task space to decide." if outcome == "over-limit" else ""))

    @tool("record_not_justifiable",
          "Record that one invoice of this message is not a justifiable business expense under the rubric, so it is "
          "not handled twice; once for each such invoice when the message holds several. Posts nothing.",
          {"type": "object", "properties": {"vendor": {"type": ["string", "null"]},
                                            "invoice_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
                                            "amount": {"type": ["number", "null"]}, "reason": {"type": "string"}},
           "required": ["reason"]})
    async def record_not_justifiable(args):
        vendor, invoice_date, amount = args.get("vendor") or "", args.get("invoice_date") or "", float(args.get("amount") or 0)
        if ledger.has(turn.message, vendor, invoice_date, amount):
            return reply("This invoice of this message is already recorded.", True)
        record(row_for(vendor, invoice_date, amount, "not-justifiable"))
        return reply("Recorded as not justifiable.")

    return [allowance_left, find_duplicate, record_reimbursement, record_not_justifiable]

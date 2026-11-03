"""General tools over a tenant's ledger policy: balance, find_match and record.

A skill's policy is `<tenant>/.imperson/<skill>/policy.yaml`. It names the people and their limits, the
period, the claim's fields, what makes two claims the same, the outcomes and the task each posts. These
tools apply it; they hold no policy of their own, and Claude passes only a claim's fields, never a figure
the policy sets.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from claude_agent_sdk import tool

from majordomo import dates

BOOK = ["handled_at", "person", "message", "outcome"]   # the columns every ledger carries before the claim's
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def money(x: float) -> str:
    return f"{x:,.2f}"


def answer(text: str, error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], **({"is_error": True} if error else {})}


def period_start(period: dict, now: datetime, tz: ZoneInfo) -> datetime:
    local = now.astimezone(tz)
    if period.get("every") == "month":
        return datetime(local.year, local.month, 1, tzinfo=tz)
    day = local.date() - timedelta(days=(local.weekday() - WEEKDAYS.index(period.get("starts", "monday").lower())) % 7)
    return datetime(day.year, day.month, day.day, tzinfo=tz)


def tools(turn, skill: str) -> list:
    folder = turn.tenant / ".imperson" / skill
    policy = yaml.safe_load((folder / "policy.yaml").read_text())
    fields = policy["claim"]["fields"]
    summed = policy["claim"]["amount"]
    columns = BOOK + list(fields)
    team = policy.get("team")
    names = {uid: name for name, uid in yaml.safe_load((turn.tenant / team).read_text())["people"].items()} if team else {}
    person = names.get(turn.sender, turn.sender)
    people = policy["people"]
    terms = people.get(person) or people.get(turn.sender) or people["default"]
    limit = terms.get("limit")
    group = "limit" if limit is not None else "no-limit"
    since = period_start(policy["period"], turn.now, ZoneInfo(policy["timezone"]))
    outcomes = policy["outcomes"]
    live = folder / policy["ledger"]
    test = turn.state / f"{skill}-ledger-test.tsv"
    own, other = (live, test) if turn.send == "live" else (test, live)

    def rows() -> list[dict]:
        return [r for p in (own, other) if p.exists() for r in csv.DictReader(p.open(newline=""), delimiter="\t")]

    def same(a: dict, b: dict, key: str) -> bool:
        if fields.get(key) == "number":
            try:
                return float(a.get(key) or 0) == float(b.get(key) or 0)
            except ValueError:
                return False
        return str(a.get(key) or "").strip().lower() == str(b.get(key) or "").strip().lower()

    def matches(claim: dict) -> list[dict]:
        rule, quiet = policy["same_claim"], {o["name"] for o in outcomes if o.get("match") is False}
        return [r for r in rows() if r["message"] != turn.message and r["outcome"] not in quiet
                and all(same(r, claim, k) for k in rule.get("all", []))
                and (not rule.get("any") or any(same(r, claim, k) for k in rule["any"]))]

    def spent() -> float:
        counted = {o["name"] for o in outcomes if o.get("spent")}
        return sum(float(r[summed] or 0) for r in rows()
                   if r["person"] == person and r["outcome"] in counted and dates.utc(r["handled_at"]) >= since)

    def write(row: dict) -> None:
        new = not own.exists()
        own.parent.mkdir(parents=True, exist_ok=True)
        with own.open("a", newline="") as fh:
            out = csv.DictWriter(fh, columns, delimiter="\t", extrasaction="ignore")
            if new:
                out.writeheader()
            out.writerow(row)
        if turn.send == "live":
            msg = f"{person}: " + ", ".join(str(row.get(k, "")) for k in fields) + f", {row['outcome']}\n\nChat message {turn.message}"
            turn.git(["-C", str(turn.tenant), "add", str(own)])
            turn.git(["-C", str(turn.tenant), "commit", "-q", "-m", msg, "--", str(own)])

    schema = {"type": "object", "properties": {k: {"type": "number" if t == "number" else "string"} for k, t in fields.items()}}

    @tool(f"{skill}_balance", "The writer's limit for this period under the business's policy, what they have used and "
          "what is left; or, for a person with no limit, the fields they are expected to give with each claim.",
          {"type": "object", "properties": {}})
    async def balance(args):
        if limit is None:
            return answer(f"{person} has no limit. Each claim needs: {', '.join(terms.get('requires', [])) or 'nothing more'}.")
        used = spent()
        return answer(f"{person}: limit {money(limit)} this period (from {since.date().isoformat()}), "
                      f"used {money(used)}, left {money(limit - used)}.")

    @tool(f"{skill}_find_match", "Earlier claims from other messages that the business's policy counts as the same claim.",
          {"type": "object", "properties": {"claim": schema}, "required": ["claim"]})
    async def find_match(args):
        found = matches(args["claim"])
        if not found:
            return answer("No earlier claim matches.")
        return answer("Matches: " + "; ".join(", ".join(f"{k} {r.get(k)}" for k in columns) for r in found))

    @tool(f"{skill}_record", "Record one claim from this message, once for each claim when it holds several, and post "
          "the task the business's policy sets for its outcome. The policy chooses the outcome; pass `outcome` only to "
          "name one the policy lets you name: " + ", ".join(o["name"] for o in outcomes if o.get("named")) + ".",
          {"type": "object", "properties": {"claim": schema, "file": {"type": "string", "description": "One of this message's files"},
                                            "outcome": {"type": "string"}}, "required": ["claim"]})
    async def record(args):
        claim = args["claim"]
        if any(r["message"] == turn.message and all(same(r, claim, k) for k in fields) for r in rows()):
            return answer("This claim of this message is already recorded.", True)
        file = args.get("file")
        if file and Path(file).resolve() not in [Path(f).resolve() for f in turn.files]:
            return answer("That file is not one saved from this message.", True)
        earlier = matches(claim)
        if earlier:
            return answer("Refused: the policy counts it as the same claim as " + ", ".join(
                f"{k} {earlier[0].get(k)}" for k in columns) + ".", True)
        projected = (limit - spent() - float(claim.get(summed) or 0)) if limit is not None else None
        if args.get("outcome"):
            chosen = next((o for o in outcomes if o["name"] == args["outcome"] and o.get("named")), None)
            if chosen is None:
                return answer(f"The policy does not let you name the outcome {args['outcome']!r}.", True)
        else:
            missing = [k for k in terms.get("requires", []) if not claim.get(k)]
            if missing:
                return answer(f"{person} must give {', '.join(missing)} with this claim: ask them.", True)
            chosen = next((o for o in outcomes if not o.get("named") and o.get("for", group) == group
                           and (o.get("min_left") is None or (projected is not None and projected >= o["min_left"]))), None)
            if chosen is None:
                return answer("No outcome in the policy fits this claim.", True)
        left = None if limit is None else projected if chosen.get("spent") else limit - spent()
        write({"handled_at": turn.now.isoformat(timespec="seconds"), "person": person, "message": turn.message,
               "outcome": chosen["name"], **{k: claim.get(k, "") for k in fields}})
        if chosen.get("task"):
            values = {**{k: claim.get(k, "") for k in fields}, "person": person,
                      "left": money(left) if left is not None else "", "over": money(-projected) if projected is not None and projected < 0 else ""}
            values[summed] = money(float(claim.get(summed) or 0))
            turn.post(policy["task_space"], chosen["task"].format_map(values), [file] if file else [])
        return answer(f"Recorded as {chosen['name']}." + (f" Left this period: {money(left)}." if left is not None else ""))

    return [balance, find_match, record]

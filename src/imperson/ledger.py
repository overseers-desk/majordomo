"""General tools over a tenant's ledger policy: balance, find_match and record.

A skill's policy is `<tenant>/.imperson/<skill>/policy.yaml`. It names the people, their groups and limits,
the period, the claim's fields, what makes two claims the same, what each scope means for each group, the
outcomes and the task each posts. These
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

BOOK = ["handled_at", "person", "message", "scope", "outcome"]   # the columns every ledger carries before the claim's
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
    limit, group = terms.get("limit"), terms["group"]
    scopes = policy["scopes"]
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
        rule, quiet = policy["same_claim"], {o["name"] for o in outcomes if o.get("refused")}
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

    def rule(scope: str):
        return scopes[scope][group]

    def describe(scope: str) -> str:
        r = rule(scope)
        if r == "refuse":
            return f"{scope}: refused"
        needs = r.get("requires", []) if isinstance(r, dict) else []
        return f"{scope}: recorded" + (f", with {', '.join(needs)}" if needs else "")

    @tool(f"{skill}_balance", "The writer's group under the business's policy, what each scope means for it, and, for a "
          "group with a limit, what they have used this period and what is left.",
          {"type": "object", "properties": {}})
    async def balance(args):
        head = f"{person} is in the group {group}. " + "; ".join(describe(s) for s in scopes) + "."
        if limit is None:
            return answer(head)
        used = spent()
        return answer(f"{head} Limit {money(limit)} this period (from {since.date().isoformat()}), "
                      f"used {money(used)}, left {money(limit - used)}.")

    @tool(f"{skill}_find_match", "Earlier claims from other messages that the business's policy counts as the same claim.",
          {"type": "object", "properties": {"claim": schema}, "required": ["claim"]})
    async def find_match(args):
        found = matches(args["claim"])
        if not found:
            return answer("No earlier claim matches.")
        return answer("Matches: " + "; ".join(", ".join(f"{k} {r.get(k)}" for k in columns) for r in found))

    @tool(f"{skill}_record", "Record one claim from this message, once for each claim when it holds several, with the "
          "scope its item falls in under the business's rubric. The policy decides from the scope and the writer's group "
          "whether it is recorded, refused, or needs a field from them, chooses the outcome and posts its task.",
          {"type": "object", "properties": {"claim": schema, "scope": {"type": "string", "enum": list(scopes)},
                                            "file": {"type": "string", "description": "One of this message's files"}},
           "required": ["claim", "scope"]})
    async def record(args):
        claim, scope = args["claim"], args["scope"]
        if scope not in scopes:
            return answer(f"The policy has no scope {scope!r}; it has {', '.join(scopes)}.", True)
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
        if rule(scope) == "refuse":
            chosen = next((o for o in outcomes if o.get("refused")), None)
            if chosen is None:
                return answer("The policy refuses this scope for this group but names no outcome to record it as.", True)
        else:
            missing = [k for k in (rule(scope).get("requires", []) if isinstance(rule(scope), dict) else []) if not claim.get(k)]
            if missing:
                return answer(f"For {scope} items {person} must give {', '.join(missing)}: ask them, and pass it on as they say it.", True)
            chosen = next((o for o in outcomes if not o.get("refused") and o.get("for", group) == group
                           and (o.get("min_left") is None or (projected is not None and projected >= o["min_left"]))), None)
            if chosen is None:
                return answer("No outcome in the policy fits this claim.", True)
        left = None if limit is None else projected if chosen.get("spent") else limit - spent()
        write({"handled_at": turn.now.isoformat(timespec="seconds"), "person": person, "message": turn.message,
               "scope": scope, "outcome": chosen["name"], **{k: claim.get(k, "") for k in fields}})
        if chosen.get("task"):
            values = {**{k: claim.get(k, "") for k in fields}, "person": person,
                      "left": money(left) if left is not None else "", "over": money(-projected) if projected is not None and projected < 0 else ""}
            values[summed] = money(float(claim.get(summed) or 0))
            values["scope"] = scope
            turn.post(policy["task_space"], chosen["task"].format_map(values), [file] if file else [])
        return answer(f"Recorded as {chosen['name']}." + (f" Left this period: {money(left)}." if left is not None else ""))

    return [balance, find_match, record]

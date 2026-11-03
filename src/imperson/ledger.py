"""General tools over a tenant's ledger policy: balance, find_match and record.

A skill's policy is `<tenant>/.imperson/<skill>/policy.yaml`. It names the people, their groups and limits,
the period, the claim's fields, what makes two claims the same, what each scope means for each group, the
outcomes and the task each posts. These tools apply it to the business's books; they hold no policy of their own,
and Claude passes only a claim's fields and the uploads it rests on, never a figure the policy sets.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from claude_agent_sdk import tool

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
FLAGS = ("refused", "duplicate", "approved")   # an outcome with one of these is chosen by that flag alone, never by the amount left


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


def periods_between(period: dict, first: datetime, last: datetime) -> int:
    """How many periods run from the one starting at ``first`` to the one starting at ``last``, both counted."""
    if period.get("every") == "month":
        return (last.year - first.year) * 12 + last.month - first.month + 1
    return (last.date() - first.date()).days // 7 + 1


def tools(turn, skill: str) -> list:
    folder = turn.tenant / ".imperson" / skill
    policy = yaml.safe_load((folder / "policy.yaml").read_text())
    fields = policy["claim"]["fields"]
    summed = policy["claim"]["amount"]
    team = policy.get("team")
    names = {uid: name for name, uid in yaml.safe_load((turn.tenant / team).read_text())["people"].items()} if team else {}
    person = names.get(turn.sender, turn.sender)
    people = policy["people"]
    terms = people.get(person) or people.get(turn.sender) or people["default"]
    limit, group = terms.get("limit"), terms["group"]
    scopes = policy["scopes"]
    tz = ZoneInfo(policy["timezone"])
    current = period_start(policy["period"], turn.now, tz)
    carried = terms.get("carry_over_from")   # unused limit rolls over from the period holding this date
    since = (period_start(policy["period"], datetime.fromisoformat(str(carried)).replace(tzinfo=tz), tz)
             if carried else current)
    if limit is not None:
        limit = limit * periods_between(policy["period"], since, current)
    outcomes = policy["outcomes"]
    alike = policy["same_claim"]
    books = turn.books
    here = {u["id"]: u.get("path") for u in turn.uploads}

    def same(a: dict, b: dict, key: str) -> bool:
        if fields.get(key) == "number":
            try:
                return float(a.get(key) or 0) == float(b.get(key) or 0)
            except ValueError:
                return False
        return str(a.get(key) or "").strip().lower() == str(b.get(key) or "").strip().lower()

    def matches(claim: dict) -> list[dict]:
        quiet = {o["name"] for o in outcomes if o.get("refused") or o.get("duplicate")}
        return [r for r in books.claims(skill) if r["message"] != turn.message and r["outcome"] not in quiet
                and all(same(r["fields"], claim, k) for k in alike.get("all", []))
                and (not alike.get("any") or any(same(r["fields"], claim, k) for k in alike["any"]))]

    def recorded(claim: dict, ids: list[int]) -> dict | None:
        """This message's claim of the same item: the vendor's name may drift between runs, so it is the policy's
        `all` fields and the amount that must agree, on the same file or on none."""
        return next((r for r in books.claims(skill, message=turn.message)
                     if all(same(r["fields"], claim, k) for k in [*alike.get("all", []), summed])
                     and (set(r["uploads"]) & set(ids) or not (r["uploads"] or ids))), None)

    def standing() -> tuple[float, float]:
        """What the person has used this period, and what is left once orders that set it are counted."""
        used, moved = books.standing(skill, turn.sender, since, [o["name"] for o in outcomes if o.get("spent")])
        return used, limit - used + moved

    def show(r: dict) -> str:
        return f"claim #{r['id']} (" + ", ".join(f"{k} {v}" for k, v in [
            ("handled_at", r["handled_at"].astimezone(tz).isoformat(timespec="minutes")),
            ("person", names.get(r["person"], r["person"])), ("message", r["message"]), ("scope", r["scope"]),
            ("outcome", r["outcome"]), *((k, r["fields"][k]) for k in fields if k in r["fields"])]) + ")"

    def saved(u: dict) -> str | None:
        """The upload's file: as saved from this message, or written out of the books for an earlier one."""
        if here.get(u["id"]):
            return str(here[u["id"]])
        data = books.content(u["id"])
        if data is None:
            return None
        out = turn.state / "work" / "uploads" / str(u["id"]) / (Path(u["name"]).name.strip(".") or "file")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return str(out)

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
          "group with a limit, what they have used and what is left, unused limit carried over where the policy says so.",
          {"type": "object", "properties": {}})
    async def balance(args):
        head = f"{person} is in the group {group}. " + "; ".join(describe(s) for s in scopes) + "."
        if limit is None:
            return answer(head)
        used, left = standing()
        return answer(f"{head} Limit {money(limit)} from {since.date().isoformat()}"
                      + (" (carried over)" if carried else " (this period)")
                      + f", used {money(used)}, left {money(left)}.")

    @tool(f"{skill}_find_match", "Earlier claims from other messages that the business's policy counts as the same claim.",
          {"type": "object", "properties": {"claim": schema}, "required": ["claim"]})
    async def find_match(args):
        found = matches(args["claim"])
        if not found:
            return answer("No earlier claim matches.")
        return answer("Matches: " + "; ".join(show(r) for r in found))

    @tool(f"{skill}_record", "Record one claim from this message, once for each claim when it holds several, with the "
          "scope its item falls in under the business's rubric and the uploads it rests on. The policy decides from the "
          "scope and the writer's group whether it is recorded, refused, or needs a field from them, chooses the outcome "
          "and posts its task. A claim the policy counts as the same as an earlier one is recorded as its duplicate.",
          {"type": "object", "properties": {
              "claim": schema, "scope": {"type": "string", "enum": list(scopes)},
              "uploads": {"type": "array", "items": {"type": "integer"},
                          "description": "The files the claim rests on, by the upload number each file's line gives: "
                                         "this message's, or an earlier message's in this conversation"}},
           "required": ["claim", "scope"]})
    async def record(args):
        claim, scope, given = args["claim"], args["scope"], args.get("uploads") or []
        if scope not in scopes:
            return answer(f"The policy has no scope {scope!r}; it has {', '.join(scopes)}.", True)
        try:
            amount = float(claim.get(summed) or 0)
            ids = list(dict.fromkeys(int(i) for i in (given if isinstance(given, list) else [given])))
        except (TypeError, ValueError):
            return answer(f"The claim's {summed} must be a number, and uploads a list of upload numbers.", True)
        found = {u["id"]: u for u in books.uploads(ids)}
        stray = [str(i) for i in ids if i not in found or not found[i]["message"].startswith(turn.space + "/")]
        if stray:
            return answer(f"Upload {', '.join(stray)} is not a file sent in this conversation.", True)
        again = recorded(claim, ids)
        if again:
            return answer(f"Already recorded as {show(again)}; nothing was written.")
        earlier = matches(claim)
        before = standing()[1] if limit is not None else None
        projected = before - amount if before is not None else None
        if earlier:
            chosen = next((o for o in outcomes if o.get("duplicate")), None)
            if chosen is None:
                return answer(f"The policy counts it as the same claim as {show(earlier[0])}, but names no outcome to "
                              "record a duplicate as.", True)
        elif rule(scope) == "refuse":
            chosen = next((o for o in outcomes if o.get("refused")), None)
            if chosen is None:
                return answer("The policy refuses this scope for this group but names no outcome to record it as.", True)
        else:
            missing = [k for k in (rule(scope).get("requires", []) if isinstance(rule(scope), dict) else []) if not claim.get(k)]
            if missing:
                return answer(f"For {scope} items {person} must give {', '.join(missing)}: ask them, and pass it on as they say it.", True)
            chosen = next((o for o in outcomes if not any(o.get(f) for f in FLAGS) and o.get("for", group) == group
                           and (o.get("min_left") is None or (projected is not None and projected >= o["min_left"]))), None)
            if chosen is None:
                return answer("No outcome in the policy fits this claim.", True)
        left = projected if chosen.get("spent") else before
        number = books.add_claim(skill=skill, account=turn.account, person=turn.sender, message=turn.message,
                                 handled_at=turn.now, scope=scope, outcome=chosen["name"], amount=amount,
                                 fields={k: claim[k] for k in fields if k in claim}, uploads=ids,
                                 duplicate_of=earlier[0]["id"] if earlier else None)
        if chosen.get("task"):
            values = {**{k: claim.get(k, "") for k in fields}, "person": person, "scope": scope, summed: money(amount),
                      "left": money(left) if left is not None else "", "claim": number,
                      "over": money(-projected) if projected is not None and projected < 0 else "",
                      "duplicate_of": earlier[0]["id"] if earlier else ""}
            turn.post(policy["task_space"], chosen["task"].format_map(values), [f for f in map(saved, found.values()) if f])
        return answer(f"Recorded as {chosen['name']}, claim #{number}"
                      + (f", a duplicate of {show(earlier[0])}" if earlier else "") + "."
                      + (f" Left this period: {money(left)}." if left is not None else ""))

    return [balance, find_match, record]

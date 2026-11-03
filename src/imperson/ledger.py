"""General tools over a tenant's ledger policy: balance, find_match, record, order and report.

A skill's policy is `<tenant>/.imperson/<skill>/policy.yaml`. It names the people, their groups and limits,
the period, the claim's fields, what makes two claims the same, what each scope means for each group, the
outcomes and the task each posts, the people with authority to give orders and the notes an order posts. These
tools apply it to the business's books; they hold no policy of their own,
and Claude passes only a claim's fields and the uploads it rests on, never a figure the policy sets nor who has authority.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from claude_agent_sdk import tool

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
FLAGS = ("refused", "duplicate", "approved")   # an outcome with one of these is chosen by that flag alone, never by the amount left
SHOWN = 50   # the claims, and the orders, a report lists: enough for a month, short enough to read in a reply


class Refusal(Exception):
    """A call the policy or the books do not allow: Claude is told why, and nothing is written."""


def guarded(handler):
    async def run(args):
        try:
            return await handler(args)
        except Refusal as exc:
            return answer(str(exc), True)
    return run


def coerced(args: dict, key: str, kind):
    """A number Claude may pass as text, "12" or "#12"; None when it is left out."""
    try:
        return None if args.get(key) in (None, "") else kind(str(args[key]).strip().lstrip("#"))
    except ValueError:
        raise Refusal(f"{key} must be a number, not {args[key]!r}.") from None


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
    people = policy["people"]
    named = {k: t["name"] for k, t in people.items() if k.startswith("users/") and t.get("name")}   # outside the team file
    scopes = policy["scopes"]
    tz = ZoneInfo(policy["timezone"])
    current = period_start(policy["period"], turn.now, tz)

    def display(uid: str) -> str:
        return names.get(uid) or named.get(uid) or uid

    def resolve(who: str) -> str:
        """The Chat id a name, a first name or an id stands for; a name that fits more than one person names none."""
        key = str(who).strip()
        found = sorted({u for u, n in [*names.items(), *named.items()]
                        if key.lower() in (n.lower(), n.lower().partition(" ")[0])})
        if len(found) > 1:
            raise Refusal(f"{key} could be {', '.join(f'{display(u)} ({u})' for u in found)}: ask the writer which "
                          "they mean, or pass the Chat id.")
        if not found and not key.startswith("users/"):
            raise Refusal(f"{key!r} is no one in the team file or the policy's people, nor a Chat id (users/…).")
        return found[0] if found else key

    def terms_of(uid: str) -> dict:
        """A person's group and, for a group with one, their limit over the periods their balance counts."""
        terms = people.get(names.get(uid)) or people.get(uid) or people["default"]
        carried = terms.get("carry_over_from")   # unused limit rolls over from the period holding this date
        since = (period_start(policy["period"], datetime.fromisoformat(str(carried)).replace(tzinfo=tz), tz)
                 if carried else current)
        limit = terms.get("limit")
        return {"id": uid, "name": display(uid), "group": terms["group"], "since": since, "carried": carried,
                "limit": None if limit is None else limit * periods_between(policy["period"], since, current)}

    me = terms_of(turn.sender)
    person, limit, group = me["name"], me["limit"], me["group"]
    authority = [str(a).strip() for a in policy.get("authority") or []]
    called = {n.lower() for n in (names.get(turn.sender), named.get(turn.sender)) if n}   # whole names, never a first name
    empowered = turn.sender in authority or any(a.lower() in called for a in authority)
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
        `all` fields and the amount that must agree, on a file in common or where either names none."""
        return next((r for r in books.claims(skill, message=turn.message)
                     if all(same(r["fields"], claim, k) for k in [*alike.get("all", []), summed])
                     and (set(r["uploads"]) & set(ids) or not r["uploads"] or not ids)), None)

    def standing(t: dict = me) -> tuple[float, float]:
        """What the person has used in their window, and what is left once orders that set it are counted."""
        used, moved = books.standing(skill, t["id"], t["since"], [o["name"] for o in outcomes if o.get("spent")])
        return used, t["limit"] - used + moved

    def show(r: dict) -> str:
        return f"claim #{r['id']} (" + ", ".join(f"{k} {v}" for k, v in [
            ("handled_at", r["handled_at"].astimezone(tz).isoformat(timespec="minutes")),
            ("person", display(r["person"])), ("message", r["message"]), ("scope", r["scope"]),
            ("outcome", r["outcome"]), *((k, r["fields"][k]) for k in fields if k in r["fields"])]) + ")"

    def saved(u: dict) -> str | None:
        """The upload's file: the copy the run saved from its message, which the books may not have kept, else one
        written out of the books."""
        if here.get(u["id"]) and Path(here[u["id"]]).exists():
            return str(here[u["id"]])
        if u["sha256"] is None:
            return None
        name = Path(u["name"]).name.strip(".") or "file"
        out = turn.state / "work" / u["message"].replace("/", "_") / name
        if out.exists() and hashlib.sha256(out.read_bytes()).hexdigest() == u["sha256"]:
            return str(out)
        if out.exists():   # another file of that message took the name, and majordomo saved this one beside it
            out = turn.state / "work" / "uploads" / str(u["id"]) / name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(books.content(u["id"]))
        return str(out)

    def fill(c: dict, left: float | None, projected: float | None) -> dict:
        """What a task's wording is filled from, for the claim ``c`` as the books hold it."""
        return {**{k: c["fields"].get(k, "") for k in fields}, "person": display(c["person"]), "scope": c["scope"],
                summed: money(c["amount"]), "left": money(left) if left is not None else "", "claim": c["id"],
                "over": money(-projected) if projected is not None and projected < 0 else "",
                "duplicate_of": c["duplicate_of"] or ""}

    def render(wording: str, key: str, values: dict) -> str:
        """A task or note filled in; one the values cannot fill is the policy's error, met before anything is written."""
        try:
            return wording.format_map(values)
        except (KeyError, IndexError, ValueError, AttributeError, TypeError) as exc:
            what = f"names {{{exc.args[0]}}}" if isinstance(exc, KeyError) else f"cannot be filled ({exc})"
            raise Refusal(f"The {skill} policy's {key} {what}; it may name " + ", ".join(f"{{{k}}}" for k in values)
                          + ". Nothing was written.") from None

    schema = {"type": "object", "properties": {k: {"type": "number" if t == "number" else "string"} for k, t in fields.items()}}

    def rule(scope: str, of: str = group):
        return scopes[scope][of]

    def describe(scope: str, of: str) -> str:
        r = rule(scope, of)
        if r == "refuse":
            return f"{scope}: refused"
        needs = r.get("requires", []) if isinstance(r, dict) else []
        return f"{scope}: recorded" + (f", with {', '.join(needs)}" if needs else "")

    def authorised(what: str) -> bool:
        if not empowered:
            raise Refusal(f"{person} ({turn.sender}) has no authority under the {skill} policy to {what}. Nothing was written.")
        return True

    @tool(f"{skill}_balance", "The writer's group under the business's policy, what each scope means for it, and, for a "
          "group with a limit, what they have used and what is left, unused limit carried over where the policy says so. "
          "A person with authority may ask it of someone else.",
          {"type": "object", "properties": {"person": {"type": "string", "description": "A name or Chat id"}}})
    @guarded
    async def balance(args):
        t = terms_of(resolve(args["person"])) if args.get("person") and authorised("see another's balance") else me
        head = f"{t['name']} is in the group {t['group']}. " + "; ".join(describe(s, t["group"]) for s in scopes) + "."
        if t["limit"] is None:
            return answer(head)
        used, left = standing(t)
        return answer(f"{head} Limit {money(t['limit'])} from {t['since'].date().isoformat()}"
                      + (" (carried over)" if t["carried"] else " (this period)")
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
    @guarded
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
        made = {"id": 0, "person": turn.sender, "scope": scope, "amount": amount,   # its number is the books' to give
                "fields": {k: claim[k] for k in fields if k in claim}, "duplicate_of": earlier[0]["id"] if earlier else None}
        task, key = chosen.get("task"), f"outcomes.{chosen['name']}.task"
        values = fill(made, left, projected)
        if task:
            render(task, key, values)   # a wording that cannot be filled stops the claim here
        number = books.add_claim(skill=skill, account=turn.account, person=turn.sender, message=turn.message,
                                 handled_at=turn.now, scope=scope, outcome=chosen["name"], amount=amount,
                                 fields=made["fields"], uploads=ids, duplicate_of=made["duplicate_of"])
        if task:
            turn.post(policy["task_space"], render(task, key, {**values, "claim": number}),
                      [f for f in map(saved, found.values()) if f])
        return answer(f"Recorded as {chosen['name']}, claim #{number}"
                      + (f", a duplicate of {show(earlier[0])}" if earlier else "") + "."
                      + (f" Left this period: {money(left)}." if left is not None else ""))

    @tool(f"{skill}_order", "An order from a person the policy gives authority: set what someone has left, approve a "
          "claim, or refuse one. Pass the order in their words.",
          {"type": "object", "properties": {
              "kind": {"type": "string", "enum": ["set_left", "approve", "refuse"]},
              "words": {"type": "string", "description": "The order as the writer wrote it"},
              "person": {"type": "string", "description": "For set_left: a name or Chat id"},
              "claim_id": {"type": "integer", "description": "For approve and refuse"},
              "amount": {"type": "number", "description": "For set_left; left out, their full limit"}},
           "required": ["kind", "words"]})
    @guarded
    async def order(args):
        kind, words = args.get("kind"), str(args.get("words") or "").strip()
        authorised("give orders")
        if kind not in ("set_left", "approve", "refuse") or not words:
            raise Refusal("An order needs its kind, set_left, approve or refuse, and the writer's words.")
        given = dict(skill=skill, account=turn.account, given_by=turn.sender, message=turn.message, given_at=turn.now,
                     kind=kind, words=words)
        if kind == "set_left":
            t = terms_of(resolve(args.get("person") or ""))
            if t["limit"] is None:
                raise Refusal(f"{t['name']} is in the group {t['group']}, which has no limit to set.")
            left, amount = standing(t)[1], coerced(args, "amount", float)
            amount = t["limit"] if amount is None else amount
            number = books.add_order(**given, person=t["id"], claim_id=None, amount=amount, delta=amount - left)
            return answer(f"{t['name']} now has {money(amount)} left, where they had {money(left)}; order #{number}.")
        n, flag = coerced(args, "claim_id", int), "approved" if kind == "approve" else "refused"
        c = next(iter(books.claims(skill, claim_id=n) if n is not None else []), None)
        target = next((o for o in outcomes if o.get(flag)), None)
        out = c and next((o for o in outcomes if o["name"] == c["outcome"]), {}).get("task")   # already in task_space
        wording = (policy.get("orders") or {}).get(kind) if out else target and target.get("task")
        why = (f"To {kind}, pass the claim's number as claim_id." if n is None else
               f"There is no {skill} claim #{n}." if c is None else
               f"Claim #{n} is recorded as a duplicate of #{c['duplicate_of']}; the order concerns #{c['duplicate_of']}."
               if kind == "approve" and c["duplicate_of"] else
               f"The {skill} policy names no outcome with {flag}: true." if target is None else
               f"Claim #{n} is already {target['name']}." if c["outcome"] == target["name"] else
               f"Claim #{n}'s task is out, and the {skill} policy has no orders.{kind} wording." if out and not wording
               else "")
        if why:
            raise Refusal(why + " Nothing was written.")
        claimant, key = terms_of(c["person"]), f"orders.{kind}" if out else f"outcomes.{target['name']}.task"

        def filled() -> tuple[float | None, str | None]:
            """What the claimant has left as the books now stand, and the wording filled in from it."""
            after = standing(claimant)[1] if claimant["limit"] is not None else None
            projected = after if after is None or target.get("spent") else after - c["amount"]
            return after, wording and render(wording, key, {**fill(c, after, projected), "given_by": person,
                                                             "words": words, "outcome": target["name"], "earlier": c["outcome"]})
        filled()   # a wording that cannot be filled stops the order here
        number = books.add_order(**given, person=c["person"], claim_id=n, amount=None, delta=None, outcome=target["name"])
        after, text = filled()
        if text:
            turn.post(policy["task_space"], text, [] if out else [f for f in map(saved, books.uploads(c["uploads"])) if f])
        return answer(f"Claim #{n} of {claimant['name']} is now {target['name']}, where it was {c['outcome']}; order "
                      f"#{number}." + (f" {claimant['name']} has {money(after)} left." if after is not None else ""))

    @tool(f"{skill}_report", "The claims recorded and the orders given, most recent first: the writer's own, or "
          "everyone's for a person with authority.",
          {"type": "object", "properties": {"person": {"type": "string", "description": "A name or Chat id"},
                                            "since": {"type": "string", "description": "An ISO date"}}})
    @guarded
    async def report(args):
        who = turn.sender if not empowered else resolve(args["person"]) if args.get("person") else None
        try:
            start = datetime.fromisoformat(str(args["since"])[:10]).replace(tzinfo=tz) if args.get("since") else None
        except ValueError:
            raise Refusal(f"since must be an ISO date, such as 2026-11-02, not {args['since']!r}.") from None
        claims = [f"#{r['id']} {display(r['person'])} {r['handled_at'].astimezone(tz).date()} "
                  + " ".join(money(r["amount"]) if k == summed else str(r["fields"][k]) for k in fields if k in r["fields"])
                  + f" {r['outcome']}" + (f" (duplicate of #{r['duplicate_of']})" if r["duplicate_of"] else "")
                  for r in books.claims(skill, person=who, since=start)[-SHOWN:][::-1]]
        given = [f"#{o['id']} {display(o['given_by'])} {o['kind']} {display(o['person'])} "
                 + (money(o["amount"]) if o["amount"] is not None else f"claim #{o['claim_id']}") + f": {o['words']}"
                 for o in books.orders(skill, person=who, since=start)[-SHOWN:][::-1]]
        whose = f"{display(who)}'s" if who else "Everyone's"
        return answer(f"{whose} claims, newest first:\n" + "\n".join(claims or ["none"])
                      + f"\n{whose} orders, newest first:\n" + "\n".join(given or ["none"]))

    return [balance, find_match, record, order, report]

"""The general ledger tools against two policies, the business's shape and a different one, over books on a MariaDB."""

import _shim  # noqa: F401

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import yaml

from imperson import ledger

ANN, BO = "users/ANN", "users/BO"
NOW = datetime(2026, 11, 4, 2, 0, tzinfo=timezone.utc)      # Wednesday 12:00 in Brisbane
MONDAY = datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)   # Monday 10:00 in Brisbane

WEEKLY = {
    "task_space": "spaces/T", "timezone": "Australia/Brisbane", "team": "team.yaml",
    "period": {"every": "week", "starts": "Monday"},
    "claim": {"fields": {"vendor": "text", "invoice_date": "date", "amount": "number", "circumstance": "text"},
              "amount": "amount"},
    "same_claim": {"all": ["invoice_date"], "any": ["vendor", "amount"]},
    "people": {"Ann": {"group": "allowance", "limit": 100}, "default": {"group": "by-approval"}},
    "scopes": {"in": {"allowance": "record", "by-approval": "record"},
               "circumstantial": {"allowance": "refuse", "by-approval": {"record": True, "requires": ["circumstance"]}},
               "out": {"allowance": "refuse", "by-approval": "refuse"}},
    "outcomes": [
        {"name": "within", "for": "allowance", "min_left": 0, "spent": True, "task": "Pay {person} {amount}, {left} left."},
        {"name": "overdrawn", "for": "allowance", "min_left": -20, "spent": True, "task": "Pay {person} {amount}, over by {over}."},
        {"name": "over-limit", "for": "allowance", "task": "Decide on {person} {amount}, over by {over}."},
        {"name": "for-approval", "for": "by-approval", "task": "Approve {person} {amount} ({scope}): {circumstance}"},
        {"name": "refused", "refused": True, "task": None},
        {"name": "duplicate", "duplicate": True, "task": "Check {person} {amount}: the same as claim #{duplicate_of}."},
        {"name": "approved", "approved": True, "spent": True, "task": None},
    ],
}
MONTHLY = {**WEEKLY, "period": {"every": "month"}, "people": {"default": {"group": "everyone"}},
           "scopes": {"any": {"everyone": {"record": True, "requires": ["circumstance"]}}},
           "outcomes": [{"name": "approved", "approved": True, "spent": True},       # reached by an order alone
                        {"name": "claimed", "spent": True, "task": "Claim from {person}: {circumstance}"}]}


def tenant(tmp_path, policy):
    root = tmp_path / "tenant"
    (root / ".imperson" / "spend").mkdir(parents=True, exist_ok=True)
    (root / "team.yaml").write_text(f"people:\n  Ann: {ANN}\n  Bo: {BO}\n")
    (root / ".imperson" / "spend" / "policy.yaml").write_text(yaml.safe_dump(policy))
    return root


def turn_for(books, root, tmp_path, *, message="spaces/D/messages/1", sender=ANN, now=NOW):
    t = SimpleNamespace(tenant=root, state=tmp_path / "state", now=now, sender=sender, message=message,
                        space=message.split("/messages/")[0], uploads=[], books=books, account="admin", posted=[])
    t.post = lambda space, text, files: t.posted.append((space, text, files))
    return t


def photo(t, content=b"jpg", n=1, error=None):
    """A file of the turn's message, saved and written to the books as the run does."""
    path = None
    if content is not None:
        path = t.state / "work" / f"{t.message.replace('/', '_')}-{n}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    u = t.books.add_upload(attachment=f"{t.message}/attachments/{n}", account="admin", message=t.message,
                           sender=t.sender, name="invoice.jpg", media_type="image/jpeg", received_at=t.now,
                           content=content, error=error)
    t.uploads.append({**u, "path": path})
    return u


def call(t, name, **args):
    (handler,) = [x.handler for x in ledger.tools(t, "spend") if x.name == f"spend_{name}"]
    result = asyncio.run(handler(args))
    return result["content"][0]["text"], bool(result.get("is_error"))


def claim(t, vendor="Bunnings", date="2026-11-03", amount=40.0, scope="in", uploads=None, **extra):
    """Record a claim on the given uploads, or on the message's photo, sent with it the first time."""
    if uploads is None:
        uploads = [u["id"] for u in t.uploads] or [photo(t)["id"]]
    return call(t, "record", claim={"vendor": vendor, "invoice_date": date, "amount": amount, **extra},
                scope=scope, uploads=uploads)


def test_the_policy_chooses_each_outcome_and_its_task(tmp_path, books):
    root = tenant(tmp_path, WEEKLY)
    t = turn_for(books, root, tmp_path)
    assert claim(t, amount=60)[0] == "Recorded as within, claim #1. Left this period: 40.00."
    assert claim(t, vendor="Officeworks", amount=50)[0].startswith("Recorded as overdrawn")
    assert claim(t, vendor="Coles", amount=30)[0].startswith("Recorded as over-limit")
    assert [(r["person"], r["outcome"], r["amount"], r["uploads"]) for r in books.claims("spend")] == [
        (ANN, "within", 60.0, [1]), (ANN, "overdrawn", 50.0, [1]), (ANN, "over-limit", 30.0, [1])]
    assert [text for _, text, _ in t.posted] == ["Pay Ann 60.00, 40.00 left.", "Pay Ann 50.00, over by 10.00.",
                                                 "Decide on Ann 30.00, over by 40.00."]
    assert {tuple(files) for _, _, files in t.posted} == {(str(t.uploads[0]["path"]),)}


def test_carried_over_limit_accumulates_from_its_start(tmp_path, books):
    root = tenant(tmp_path, {**WEEKLY, "people": {**WEEKLY["people"], "Ann": {"group": "allowance", "limit": 100,
                                                                              "carry_over_from": "2026-10-20"}}})
    claim(turn_for(books, root, tmp_path, now=datetime(2026, 10, 21, 2, 0, tzinfo=timezone.utc)), amount=30)
    t = turn_for(books, root, tmp_path, message="spaces/D/messages/2")
    assert "Limit 300.00 from 2026-10-19 (carried over), used 30.00, left 270.00" in call(t, "balance")[0]


def test_balance_counts_only_spent_outcomes_in_this_period(tmp_path, books):
    root = tenant(tmp_path, WEEKLY)
    claim(turn_for(books, root, tmp_path, now=datetime(2026, 11, 1, 2, 0, tzinfo=timezone.utc)), amount=90)
    t = turn_for(books, root, tmp_path, message="spaces/D/messages/2", now=MONDAY)
    assert "used 0.00, left 100.00" in call(t, "balance")[0]


def test_the_scope_table_decides_for_each_group(tmp_path, books):
    root = tenant(tmp_path, WEEKLY)
    ann = turn_for(books, root, tmp_path)
    assert "group allowance" in call(ann, "balance")[0] and "circumstantial: refused" in call(ann, "balance")[0]
    assert claim(ann, vendor="Cafe", scope="circumstantial")[0] == "Recorded as refused, claim #1. Left this period: 100.00."
    bo = turn_for(books, root, tmp_path, sender=BO, message="spaces/E/messages/2")
    assert "circumstantial: recorded, with circumstance" in call(bo, "balance")[0]
    text, error = claim(bo, vendor="Pizza", scope="circumstantial")
    assert error and "must give circumstance" in text and bo.posted == []
    answered = turn_for(books, root, tmp_path, sender=BO, message="spaces/E/messages/3")   # the photo came earlier
    assert claim(answered, vendor="Pizza", scope="circumstantial", circumstance="team lunch",
                 uploads=[bo.uploads[0]["id"]])[0] == "Recorded as for-approval, claim #2."
    assert claim(bo, vendor="Paper", amount=8, scope="in")[0] == "Recorded as for-approval, claim #3."
    assert claim(bo, vendor="Yacht", amount=9, scope="out")[0] == "Recorded as refused, claim #4."
    assert [t for _, t, _ in answered.posted + bo.posted] == ["Approve Bo 40.00 (circumstantial): team lunch",
                                                              "Approve Bo 8.00 (in): "]
    (sent,) = answered.posted[0][2]
    assert Path(sent).name == "invoice.jpg" and Path(sent).read_bytes() == b"jpg"
    assert [(r["person"], r["scope"]) for r in books.claims("spend")] == [
        (ANN, "circumstantial"), (BO, "circumstantial"), (BO, "in"), (BO, "out")]


def test_same_claim_rules(tmp_path, books):
    root = tenant(tmp_path, WEEKLY)
    t = turn_for(books, root, tmp_path)
    claim(t, amount=10)
    text, error = claim(t, vendor="BUNNINGS PTY", amount=10)          # this message read again, the vendor read anew
    assert not error and text.startswith("Already recorded as claim #1 (") and len(books.claims("spend")) == 1
    assert claim(t, amount=20)[0].startswith("Recorded as within, claim #2")   # same shop and day, another receipt in one photo
    assert claim(t, amount=10, uploads=[photo(t, b"other", n=2)["id"]])[0].startswith("Recorded as within, claim #3")
    other = turn_for(books, root, tmp_path, message="spaces/D/messages/2")
    text, error = claim(other, vendor="BUNNINGS ", amount=99)
    assert not error and text.startswith("Recorded as duplicate, claim #4, a duplicate of claim #1 (")
    assert text.endswith("Left this period: 60.00.") and other.posted[0][1] == "Check Ann 99.00: the same as claim #1."
    assert [(r["outcome"], r["duplicate_of"]) for r in books.claims("spend", message=other.message)] == [("duplicate", 1)]
    assert "used 40.00, left 60.00" in call(other, "balance")[0]
    found = call(other, "find_match", claim={"vendor": "bunnings", "invoice_date": "2026-11-03", "amount": 5})[0]
    assert found.startswith("Matches: claim #1 (handled_at 2026-11-04T12:00+10:00, person Ann, message spaces/D/messages/1")
    assert "claim #4" not in found
    assert "No earlier claim matches" in call(other, "find_match", claim={"vendor": "Aldi", "invoice_date": "2026-11-05", "amount": 3})[0]


def test_a_policy_with_no_duplicate_outcome_refuses_to_record_one(tmp_path, books):
    root = tenant(tmp_path, MONTHLY)
    claim(turn_for(books, root, tmp_path), scope="any", circumstance="stationery")
    text, error = claim(turn_for(books, root, tmp_path, message="spaces/D/messages/2"), scope="any", circumstance="again")
    assert error and "names no outcome to record a duplicate as" in text and len(books.claims("spend")) == 1


def test_a_claim_rests_only_on_files_sent_in_its_conversation(tmp_path, books):
    root = tenant(tmp_path, WEEKLY)
    elsewhere = photo(turn_for(books, root, tmp_path, message="spaces/OTHER/messages/9"))
    t = turn_for(books, root, tmp_path)
    text, error = claim(t, uploads=[elsewhere["id"]])
    assert error and f"Upload {elsewhere['id']} is not a file sent in this conversation" in text
    assert claim(t, uploads=[99])[1] and books.claims("spend") == []
    own = photo(t, b"own")
    assert claim(t, uploads=[str(own["id"])])[0].startswith("Recorded as within")    # the number passed as text
    assert books.claims("spend")[0]["uploads"] == [own["id"]]


def test_the_same_photo_sent_twice_is_one_file_and_two_uploads(tmp_path, books):
    root = tenant(tmp_path, WEEKLY)
    first = photo(turn_for(books, root, tmp_path, now=MONDAY.astimezone(ZoneInfo("Australia/Brisbane"))))
    t = turn_for(books, root, tmp_path, message="spaces/D/messages/2")
    second = photo(t)
    assert first["identical_to"] is None and second["file_id"] == first["file_id"] is not None
    assert second["identical_to"] == {"id": first["id"], "message": "spaces/D/messages/1", "received_at": MONDAY}
    assert photo(t)["id"] == second["id"] and len(books.uploads([first["id"], second["id"], second["id"] + 1])) == 2
    assert books.content(second["id"]) == b"jpg"


def test_a_file_that_could_not_be_fetched_is_filled_in_when_it_is(tmp_path, books):
    t = turn_for(books, tenant(tmp_path, WEEKLY), tmp_path)
    failed = photo(t, None, error="majordomo: invoice.jpg: HTTP 500")
    assert (failed["file_id"], failed["error"], books.content(failed["id"])) == (None, "majordomo: invoice.jpg: HTTP 500", None)
    filled = photo(t)
    assert filled["id"] == failed["id"] and filled["error"] is None and books.content(filled["id"]) == b"jpg"


def test_a_set_left_order_moves_the_balance_by_its_delta(tmp_path, books):
    root = tenant(tmp_path, WEEKLY)
    claim(turn_for(books, root, tmp_path), amount=60)
    t = turn_for(books, root, tmp_path, message="spaces/D/messages/2")

    def order(left, delta, at=NOW):
        books.add_order(skill="spend", account="admin", given_by=BO, message="spaces/T/messages/5", given_at=at,
                        kind="set_left", person=ANN, claim_id=None, amount=left, delta=delta, words=f"Ann has {left} left")
    order(500, 460, at=datetime(2026, 11, 1, tzinfo=timezone.utc))      # last week's
    order(70, 70 - 40)                                                  # above the 40 left
    assert "used 60.00, left 70.00" in call(t, "balance")[0]
    order(25, 25 - 70)                                                  # below the 70 then left
    assert "used 60.00, left 25.00" in call(t, "balance")[0]
    assert claim(t, vendor="Coles", amount=20)[0] == "Recorded as within, claim #2. Left this period: 5.00."
    assert [o["delta"] for o in books.orders("spend", person=ANN, since=MONDAY)] == [30.0, -45.0]


def test_a_different_policy_needs_no_change_in_code(tmp_path, books):
    root = tenant(tmp_path, MONTHLY)
    t = turn_for(books, root, tmp_path)
    assert "any: recorded, with circumstance" in call(t, "balance")[0]
    assert claim(t, scope="any", circumstance="stationery")[0] == "Recorded as claimed, claim #1."
    assert t.posted[0][1] == "Claim from Ann: stationery"

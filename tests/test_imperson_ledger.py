"""The general ledger tools against two policies, the business's shape and a different one, over books on a MariaDB."""

import _shim  # noqa: F401

import asyncio
import contextlib
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import yaml

from imperson import ledger
from imperson.books import Books

ANN, BO, SAM = "users/ANN", "users/BO", "users/SAM"
NOW = datetime(2026, 11, 4, 2, 0, tzinfo=timezone.utc)      # Wednesday 12:00 in Brisbane
MONDAY = datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)   # Monday 10:00 in Brisbane

WEEKLY = {
    "task_space": "spaces/T", "timezone": "Australia/Brisbane", "team": "team.yaml", "authority": ["Ann"],
    "period": {"every": "week", "starts": "Monday"},
    "claim": {"fields": {"vendor": "text", "invoice_date": "date", "amount": "number", "circumstance": "text"},
              "amount": "amount"},
    "same_claim": {"all": ["invoice_date"], "any": ["vendor", "amount"]},
    "people": {"Ann": {"group": "allowance", "limit": 100}, SAM: {"group": "allowance", "limit": 50, "name": "Sam Example"},
               "default": {"group": "by-approval"}},
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
        {"name": "approved", "approved": True, "spent": True, "task": "Pay {person} {amount}, approved by {given_by}."},
    ],
    "orders": {"approve": "Claim #{claim} of {person} approved by {given_by}, was {earlier}: {words}",
               "refuse": "Claim #{claim} of {person} refused by {given_by}, was {earlier}: {words}"},
}
MONTHLY = {**WEEKLY, "period": {"every": "month"}, "people": {"default": {"group": "everyone"}},
           "scopes": {"any": {"everyone": {"record": True, "requires": ["circumstance"]}}},
           "outcomes": [{"name": "approved", "approved": True, "spent": True},       # reached by an order alone
                        {"name": "claimed", "spent": True, "task": "Claim from {person}: {circumstance}"}]}


def tenant(tmp_path, policy):
    root = tmp_path / "tenant"
    (root / ".imperson" / "spend").mkdir(parents=True, exist_ok=True)
    (root / "team.yaml").write_text(f"people:\n  Ann: {ANN}\n  Bo: {BO}\n")
    (root / ".imperson" / "spend" / "policy.yaml").write_text(yaml.safe_dump(policy, sort_keys=False))
    return root


def turn_for(books, root, tmp_path, *, message="spaces/D/messages/1", sender=ANN, now=NOW):
    t = SimpleNamespace(tenant=root, state=tmp_path / "state", now=now, sender=sender, message=message,
                        space=message.split("/messages/")[0], uploads=[], books=books, account="admin", posted=[])
    t.post = lambda space, text, files: t.posted.append((space, text, files))
    return t


def photo(t, content=b"jpg", n=1, error=None):
    """A file of the turn's message, saved where the run saves it, a second invoice.jpg as invoice-2.jpg, and written to the books."""
    path = None
    if content is not None:
        path = t.state / "work" / t.message.replace("/", "_") / ("invoice.jpg" if n == 1 else f"invoice-{n}.jpg")
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
    again = call(t, "record", claim={"vendor": "Bunnings", "invoice_date": "2026-11-03", "amount": 10}, scope="in")
    assert again[0].startswith("Already recorded as claim #1 (") and len(books.claims("spend")) == 1   # retried without uploads
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


def test_the_same_photo_from_another_conversation_is_kept_once_and_never_named(tmp_path, books):
    root = tenant(tmp_path, WEEKLY)
    elsewhere = photo(turn_for(books, root, tmp_path, sender=BO, message="spaces/E/messages/1"))
    first = photo(turn_for(books, root, tmp_path, message="spaces/D/messages/2"))
    assert first["file_id"] == elsewhere["file_id"] and first["identical_to"] is None
    assert photo(turn_for(books, root, tmp_path, message="spaces/D/messages/3"))["identical_to"]["id"] == first["id"]
    assert photo(turn_for(books, root, tmp_path, message="spaces/e/messages/4"))["identical_to"] is None   # another space


def test_two_attachment_names_differing_only_in_case_are_two_uploads(books):
    first, second = (books.add_upload(attachment=f"spaces/D/messages/1/attachments/{n}", account="admin",
                                      message="spaces/D/messages/1", sender=ANN, name="invoice.jpg",
                                      media_type="image/jpeg", received_at=NOW, content=c, error=None)
                     for n, c in (("Ab", b"one"), ("aB", b"two")))
    assert first["id"] != second["id"] and books.content(second["id"]) == b"two"


def test_a_file_that_could_not_be_fetched_is_filled_in_when_it_is(tmp_path, books):
    t = turn_for(books, tenant(tmp_path, WEEKLY), tmp_path)
    failed = photo(t, None, error="majordomo: invoice.jpg: HTTP 500")
    assert (failed["file_id"], failed["error"], books.content(failed["id"])) == (None, "majordomo: invoice.jpg: HTTP 500", None)
    assert photo(t, None, error="majordomo: invoice.jpg: HTTP 503")["error"] == "majordomo: invoice.jpg: HTTP 503"
    filled = photo(t)
    assert filled["id"] == failed["id"] and filled["error"] is None and books.content(filled["id"]) == b"jpg"
    assert photo(t, None, error="majordomo: invoice.jpg: HTTP 500")["error"] is None and books.content(filled["id"]) == b"jpg"


def test_set_left_makes_what_is_left_the_figure_ordered_and_a_reset_makes_it_the_limit(tmp_path, books):
    root = tenant(tmp_path, WEEKLY)
    sam = turn_for(books, root, tmp_path, sender=SAM, message="spaces/S/messages/1")
    claim(sam, amount=30)
    ann = turn_for(books, root, tmp_path, message="spaces/D/messages/2")
    call(turn_for(books, root, tmp_path, now=datetime(2026, 11, 1, tzinfo=timezone.utc)), "order", kind="set_left",
         person="Sam", amount=500, words="last week's")
    assert call(ann, "order", kind="set_left", person="Sam", amount="45", words="Sam has 45 left")[0] == (
        "Sam Example now has 45.00 left, where they had 20.00; order #2.")
    assert "Sam Example is in the group allowance" in call(sam, "balance")[0] and "left 45.00" in call(sam, "balance")[0]
    call(ann, "order", kind="set_left", person="sam example", amount=5, words="make it 5")
    assert "used 30.00, left 5.00" in call(ann, "balance", person="users/SAM")[0]
    assert claim(sam, vendor="Coles", amount=20)[0] == "Recorded as overdrawn, claim #2. Left this period: -15.00."
    assert call(ann, "order", kind="set_left", person="SAM", words="reset Sam's allowance")[0].startswith(
        "Sam Example now has 50.00 left, where they had -15.00")
    assert [(o["given_by"], o["amount"], o["delta"]) for o in books.orders("spend", person=SAM, since=MONDAY)] == [
        (ANN, 45.0, 25.0), (ANN, 5.0, -40.0), (ANN, 50.0, 65.0)]
    assert "left 50.00" in call(sam, "balance")[0]


def test_orders_and_questions_about_others_from_someone_without_authority_are_refused(tmp_path, books):
    root = tenant(tmp_path, WEEKLY)
    claim(turn_for(books, root, tmp_path, sender=BO, message="spaces/E/messages/1"), scope="out")
    bo = turn_for(books, root, tmp_path, sender=BO, message="spaces/E/messages/2")
    for args in ({"kind": "set_left", "person": "Bo", "amount": 500}, {"kind": "approve", "claim_id": 1}):
        text, error = call(bo, "order", words="pay me", **args)
        assert error and text.startswith("Bo (users/BO) has no authority under the spend policy")
    assert call(bo, "balance", person="Ann")[1]
    assert books.orders("spend") == [] and books.claims("spend")[0]["outcome"] == "refused" and bo.posted == []


def test_authority_is_held_by_a_whole_name_from_the_team_file_or_the_policy_and_never_by_a_first_name(tmp_path, books):
    people = {**WEEKLY["people"], "users/PAT": {"group": "by-approval", "name": "Pat Example"}}
    root = tenant(tmp_path, {**WEEKLY, "people": people, "authority": ["ann", "PAT EXAMPLE", "Sam"]})
    for sender in (ANN, "users/PAT", SAM):
        call(turn_for(books, root, tmp_path, sender=sender), "order", kind="set_left", person="Sam Example", words="reset")
    assert [o["given_by"] for o in books.orders("spend")] == [ANN, "users/PAT"]


def test_a_name_that_fits_more_than_one_person_names_none(tmp_path, books):
    root = tenant(tmp_path, {**WEEKLY, "people": {**WEEKLY["people"], "users/SAM2": {"group": "allowance", "limit": 10,
                                                                                    "name": "Sam Other"}}})
    ann = turn_for(books, root, tmp_path)
    text, error = call(ann, "order", kind="set_left", person="sam", amount=1, words="Sam has 1 left")
    assert error and text.startswith("sam could be Sam Example (users/SAM), Sam Other (users/SAM2): ask the writer")
    assert call(ann, "order", kind="set_left", person="Sam Other", amount=1, words="1 left")[0].startswith("Sam Other now")
    assert call(ann, "balance", person="Zed")[1] and len(books.orders("spend")) == 1


def test_approving_a_refused_claim_posts_its_task_with_its_file_and_spends_it(tmp_path, books):
    root = tenant(tmp_path, WEEKLY)
    sam = turn_for(books, root, tmp_path, sender=SAM, message="spaces/S/messages/1")
    claim(sam, vendor="Cafe", scope="circumstantial")                  # refused for the allowance group
    sent = sam.uploads[0]["path"]
    sent.unlink()                                                      # the run's copy is gone, as on another host
    ann = turn_for(books, root, tmp_path, message="spaces/D/messages/2")
    assert call(ann, "order", kind="approve", claim_id="#1", words="pay Sam's cafe")[0] == (
        "Claim #1 of Sam Example is now approved, where it was refused; order #1. Sam Example has 10.00 left.")
    assert [(o["kind"], o["person"], o["claim_id"], o["given_by"], o["message"], o["words"]) for o in books.orders("spend")] == [
        ("approve", SAM, 1, ANN, "spaces/D/messages/2", "pay Sam's cafe")]
    assert ann.posted == [("spaces/T", "Pay Sam Example 40.00, approved by Ann.", [str(sent)])] and sent.read_bytes() == b"jpg"
    text, error = call(ann, "order", kind="approve", claim_id=1, words="pay it again")
    assert error and "already approved" in text and len(books.orders("spend")) == 1 and len(ann.posted) == 1


def test_an_order_on_a_claim_whose_task_is_out_posts_the_orders_wording_instead(tmp_path, books):
    root = tenant(tmp_path, WEEKLY)
    claim(turn_for(books, root, tmp_path), amount=60)                                          # within: "Pay Ann" is out
    claim(turn_for(books, root, tmp_path, sender=BO, message="spaces/E/messages/2"), vendor="Paper", amount=8)  # for-approval
    ann = turn_for(books, root, tmp_path, message="spaces/D/messages/3")
    assert call(ann, "order", kind="refuse", claim_id=1, words="not that one")[0] == (
        "Claim #1 of Ann is now refused, where it was within; order #1. Ann has 100.00 left.")
    call(ann, "order", kind="approve", claim_id=2, words="fine, pay Bo")
    assert ann.posted == [("spaces/T", "Claim #1 of Ann refused by Ann, was within: not that one", []),
                          ("spaces/T", "Claim #2 of Bo approved by Ann, was for-approval: fine, pay Bo", [])]
    bare = turn_for(books, tenant(tmp_path, {**WEEKLY, "orders": {}}), tmp_path, message="spaces/D/messages/4")
    text, error = call(bare, "order", kind="refuse", claim_id=2, words="no, not Bo's")
    assert error and "no orders.refuse wording" in text and len(books.orders("spend")) == 2 and bare.posted == []
    assert [r["outcome"] for r in books.claims("spend")] == ["refused", "approved"]


def test_an_approval_of_a_duplicate_is_refused_and_names_the_claim_it_repeats(tmp_path, books):
    root = tenant(tmp_path, WEEKLY)
    claim(turn_for(books, root, tmp_path), amount=60)
    claim(turn_for(books, root, tmp_path, sender=BO, message="spaces/E/messages/2"), amount=60)   # the same invoice again
    text, error = call(turn_for(books, root, tmp_path, message="spaces/D/messages/3"), "order", kind="approve",
                       claim_id=2, words="pay Bo")
    assert error and text == "Claim #2 is recorded as a duplicate of #1; the order concerns #1. Nothing was written."
    assert books.orders("spend") == [] and [r["outcome"] for r in books.claims("spend")] == ["within", "duplicate"]


def test_a_wording_naming_a_value_the_tools_do_not_give_writes_nothing(tmp_path, books):
    outcomes = [{**o, "task": "Pay {person} {amount} by {due}."} if o["name"] == "within" else o for o in WEEKLY["outcomes"]]
    root = tenant(tmp_path, {**WEEKLY, "outcomes": outcomes, "orders": {"refuse": "Refused: {reason}"}})
    ann = turn_for(books, root, tmp_path)
    text, error = claim(ann, amount=10)
    assert error and text.startswith("The spend policy's outcomes.within.task names {due}; it may name {vendor}, ")
    assert books.claims("spend") == [] and ann.posted == []
    claim(turn_for(books, root, tmp_path, sender=BO, message="spaces/E/messages/2"), vendor="Paper", amount=8)
    text, error = call(ann, "order", kind="refuse", claim_id=1, words="no")
    assert error and text.startswith("The spend policy's orders.refuse names {reason};") and "{earlier}" in text
    assert books.orders("spend") == [] and books.claims("spend")[0]["outcome"] == "for-approval" and ann.posted == []


def test_a_report_shows_everyone_to_a_person_with_authority_and_their_own_to_anyone_else(tmp_path, books):
    root = tenant(tmp_path, WEEKLY)
    claim(turn_for(books, root, tmp_path), amount=60)
    bo = turn_for(books, root, tmp_path, sender=BO, message="spaces/E/messages/2")
    claim(bo, vendor="Paper", amount=8)
    ann = turn_for(books, root, tmp_path, message="spaces/D/messages/3")
    ann.account = "owner"                               # another account of the business reads the same books
    call(ann, "order", kind="refuse", claim_id=2, words="no paper")
    assert call(ann, "report")[0] == (
        "Everyone's claims, newest first:\n#2 Bo 2026-11-04 Paper 2026-11-03 8.00 refused\n"
        "#1 Ann 2026-11-04 Bunnings 2026-11-03 60.00 within\nEveryone's orders, newest first:\n#1 Ann refuse Bo claim #2: no paper")
    assert call(bo, "report", person="Ann")[0] == ("Bo's claims, newest first:\n#2 Bo 2026-11-04 Paper 2026-11-03 8.00 refused"
                                                   "\nBo's orders, newest first:\n#1 Ann refuse Bo claim #2: no paper")
    assert call(ann, "report", person="Ann", since="2026-11-05")[0].count("none") == 2


def test_a_file_named_like_another_in_its_message_is_posted_as_its_own(tmp_path, books):
    root = tenant(tmp_path, WEEKLY)
    bo = turn_for(books, root, tmp_path, sender=BO, message="spaces/E/messages/1")
    photo(bo), photo(bo, b"two", n=2)                   # both invoice.jpg; the second saved beside the first
    later = turn_for(books, root, tmp_path, sender=BO, message="spaces/E/messages/2")
    claim(later, uploads=[2])
    assert Path(later.posted[0][2][0]).read_bytes() == b"two"


def test_a_file_the_books_could_not_keep_is_posted_from_this_message_s_copy_and_not_once_it_is_gone(tmp_path, books,
                                                                                                    monkeypatch):
    monkeypatch.setattr(books, "packet", 4096)
    t = turn_for(books, tenant(tmp_path, WEEKLY), tmp_path)
    photo(t, b"x" * 5000)
    claim(t, amount=10)
    copy = t.uploads[0]["path"]
    copy.unlink()
    claim(t, vendor="Officeworks", amount=5)
    assert [files for _, _, files in t.posted] == [[str(copy)], []]


def test_a_connection_that_dies_mid_query_raises_the_error_that_killed_it_not_the_failed_rollback(monkeypatch):
    import pymysql

    lost = pymysql.err.OperationalError(2013, "Lost connection to MySQL server during query")

    class Dead:
        def cursor(self):
            return contextlib.nullcontext(SimpleNamespace(execute=lambda *a: (_ for _ in ()).throw(lost)))

        def rollback(self):
            raise pymysql.err.InterfaceError(0, "")
    books = Books({}, "imperson_pytest_")
    monkeypatch.setattr(books, "_connect", Dead)
    with pytest.raises(pymysql.err.OperationalError) as raised:
        books.claims("spend")
    assert raised.value is lost


def test_a_file_larger_than_the_server_takes_is_an_upload_without_its_file(tmp_path, books, monkeypatch):
    t = turn_for(books, tenant(tmp_path, WEEKLY), tmp_path)
    assert books.packet >= 1024                         # as the server reported it
    monkeypatch.setattr(books, "packet", 4096)
    big = photo(t, b"x" * 5000)
    assert (big["file_id"], books.content(big["id"])) == (None, None) and big["error"].startswith("the file of 5,000 bytes")
    assert big["error"].endswith("exceeds the server's limit of 4,096") and photo(t, b"small", n=2)["file_id"] is not None


def test_a_different_policy_needs_no_change_in_code(tmp_path, books):
    root = tenant(tmp_path, MONTHLY)
    t = turn_for(books, root, tmp_path)
    assert "any: recorded, with circumstance" in call(t, "balance")[0]
    assert claim(t, scope="any", circumstance="stationery")[0] == "Recorded as claimed, claim #1."
    assert t.posted[0][1] == "Claim from Ann: stationery"

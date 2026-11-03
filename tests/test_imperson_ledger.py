"""The general ledger tools against two policies: the business's shape, and a different one."""

import _shim  # noqa: F401

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import yaml

from imperson import ledger

ANN, BO, STRANGER = "users/ANN", "users/BO", "users/X"
NOW = datetime(2026, 11, 4, 2, 0, tzinfo=timezone.utc)      # Wednesday 12:00 in Brisbane
MONDAY = datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)   # Monday 10:00 in Brisbane

WEEKLY = {
    "task_space": "spaces/T", "timezone": "Australia/Brisbane", "team": "team.yaml",
    "period": {"every": "week", "starts": "Monday"}, "ledger": "ledger.tsv",
    "claim": {"fields": {"vendor": "text", "invoice_date": "date", "amount": "number", "reason": "text"},
              "amount": "amount"},
    "same_claim": {"all": ["invoice_date"], "any": ["vendor", "amount"]},
    "people": {"Ann": {"limit": 100}, "default": {"requires": ["reason"]}},
    "outcomes": [
        {"name": "within", "for": "limit", "min_left": 0, "spent": True, "task": "Pay {person} {amount}, {left} left."},
        {"name": "overdrawn", "for": "limit", "min_left": -20, "spent": True, "task": "Pay {person} {amount}, over by {over}."},
        {"name": "over-limit", "for": "limit", "task": "Decide on {person} {amount}, over by {over}."},
        {"name": "reason-given", "for": "no-limit", "task": "Pay {person} {amount}: {reason}"},
        {"name": "not-justifiable", "named": True, "match": False, "task": None},
    ],
}
MONTHLY = {**WEEKLY, "period": {"every": "month"}, "people": {"default": {"requires": ["reason"]}},
           "outcomes": [{"name": "claimed", "spent": True, "task": "Claim from {person}: {reason}"}]}


def tenant(tmp_path, policy):
    root = tmp_path / "tenant"
    (root / ".imperson" / "spend").mkdir(parents=True, exist_ok=True)
    (root / "team.yaml").write_text(f"people:\n  Ann: {ANN}\n  Bo: {BO}\n")
    (root / ".imperson" / "spend" / "policy.yaml").write_text(yaml.safe_dump(policy))
    return root


def turn_for(root, tmp_path, *, message="spaces/D/messages/1", sender=ANN, send="live", now=NOW):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    photo = tmp_path / f"{message.replace('/', '_')}.jpg"
    photo.write_bytes(b"jpg")
    t = SimpleNamespace(tenant=root, state=state, now=now, send=send, sender=sender, message=message,
                        files=[photo], posted=[], gits=[])
    t.post = lambda space, text, files: t.posted.append((space, text, files))
    t.git = lambda args: t.gits.append(args)
    return t


def call(t, name, **args):
    (handler,) = [x.handler for x in ledger.tools(t, "spend") if x.name == f"spend_{name}"]
    result = asyncio.run(handler(args))
    return result["content"][0]["text"], bool(result.get("is_error"))


def claim(t, vendor="Bunnings", date="2026-11-03", amount=40.0, **extra):
    return call(t, "record", claim={"vendor": vendor, "invoice_date": date, "amount": amount, **extra},
                file=str(t.files[0]))


def lines(path):
    rows = path.read_text().splitlines()
    return [dict(zip(rows[0].split("\t"), r.split("\t"))) for r in rows[1:]]


def live(root):
    return root / ".imperson" / "spend" / "ledger.tsv"


def test_the_policy_chooses_each_outcome_and_its_task(tmp_path):
    root = tenant(tmp_path, WEEKLY)
    t = turn_for(root, tmp_path)
    assert claim(t, amount=60)[0] == "Recorded as within. Left this period: 40.00."
    assert claim(t, vendor="Officeworks", amount=50)[0].startswith("Recorded as overdrawn")
    assert claim(t, vendor="Coles", amount=30)[0].startswith("Recorded as over-limit")
    assert [r["outcome"] for r in lines(live(root))] == ["within", "overdrawn", "over-limit"]
    assert [text for _, text, _ in t.posted] == ["Pay Ann 60.00, 40.00 left.", "Pay Ann 50.00, over by 10.00.",
                                                 "Decide on Ann 30.00, over by 40.00."]
    assert len(t.gits) == 6


def test_balance_counts_only_spent_outcomes_in_this_period(tmp_path):
    root = tenant(tmp_path, WEEKLY)
    claim(turn_for(root, tmp_path, now=datetime(2026, 11, 1, 2, 0, tzinfo=timezone.utc)), amount=90)
    t = turn_for(root, tmp_path, message="spaces/D/messages/2", now=MONDAY)
    assert "used 0.00, left 100.00" in call(t, "balance")[0]


def test_a_person_with_no_limit_gives_the_required_field(tmp_path):
    root = tenant(tmp_path, WEEKLY)
    t = turn_for(root, tmp_path, sender=STRANGER)
    assert "Each claim needs: reason" in call(t, "balance")[0]
    text, error = claim(t)
    assert error and "must give reason" in text and t.posted == []
    assert claim(t, reason="cleaning supplies")[0] == "Recorded as reason-given."
    assert t.posted[0][1] == "Pay users/X 40.00: cleaning supplies"


def test_a_named_outcome_records_and_posts_nothing(tmp_path):
    root = tenant(tmp_path, WEEKLY)
    t = turn_for(root, tmp_path)
    text, error = call(t, "record", claim={"vendor": "Cafe", "invoice_date": "2026-11-03", "amount": 12},
                       outcome="not-justifiable")
    assert not error and t.posted == [] and lines(live(root))[0]["outcome"] == "not-justifiable"
    assert call(t, "record", claim={"vendor": "X", "invoice_date": "2026-11-03", "amount": 1}, outcome="within")[1]


def test_same_claim_rules(tmp_path):
    root = tenant(tmp_path, WEEKLY)
    t = turn_for(root, tmp_path)
    claim(t, amount=10)
    assert claim(t, amount=10)[1]                                   # this message, again
    assert not claim(t, amount=20)[1]                               # same shop and day, another receipt in one photo
    other = turn_for(root, tmp_path, message="spaces/D/messages/2")
    text, error = claim(other, vendor="BUNNINGS ", amount=99)
    assert error and "same claim" in text
    assert "No earlier claim matches" in call(other, "find_match", claim={"vendor": "Aldi", "invoice_date": "2026-11-05", "amount": 3})[0]


def test_a_file_must_be_from_the_message(tmp_path):
    root = tenant(tmp_path, WEEKLY)
    t = turn_for(root, tmp_path)
    text, error = call(t, "record", claim={"vendor": "V", "invoice_date": "2026-11-03", "amount": 1}, file=str(tmp_path / "x.jpg"))
    assert error and "not one saved" in text


@pytest.mark.parametrize("mode", ["off", "test"])
def test_outside_live_the_test_ledger_is_written_and_nothing_committed(tmp_path, mode):
    root = tenant(tmp_path, WEEKLY)
    t = turn_for(root, tmp_path, send=mode)
    claim(t)
    assert t.gits == [] and not live(root).exists() and (t.state / "spend-ledger-test.tsv").exists()


def test_a_different_policy_needs_no_change_in_code(tmp_path):
    root = tenant(tmp_path, MONTHLY)
    t = turn_for(root, tmp_path)
    assert "Each claim needs: reason" in call(t, "balance")[0]
    assert claim(t, reason="stationery")[0] == "Recorded as claimed."
    assert t.posted[0][1] == "Claim from Ann: stationery"

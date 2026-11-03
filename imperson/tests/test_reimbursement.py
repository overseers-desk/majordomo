"""The reimbursement tools against a tenant built in a temporary folder."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import reimbursement

ANN, BO, STRANGER = "users/ANN", "users/BO", "users/X"
NOW = datetime(2026, 11, 4, 2, 0, tzinfo=timezone.utc)      # Wednesday 12:00 in Brisbane
MONDAY = datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)   # Monday 10:00 in Brisbane


@pytest.fixture
def tenant(tmp_path):
    root = tmp_path / "tenant"
    (root / ".reviewagent").mkdir(parents=True)
    (root / ".imperson" / "reimbursement").mkdir(parents=True)
    (root / ".reviewagent" / "team.yaml").write_text(f"people:\n  Ann: {ANN}\n  Bo: {BO}\n")
    (root / ".reviewagent" / "settings.yaml").write_text("timezone: Australia/Brisbane\n")
    (root / ".imperson" / "reimbursement" / "settings.yaml").write_text(
        "task_space: spaces/DMS\nweek_starts: Monday\noverdraft_limit: 20\nallowances: {Ann: 100, Bo: 100}\n")
    return root


def make_turn(tenant, tmp_path, *, message="spaces/D1/messages/1", sender=ANN, send="live", now=NOW):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    photo = tmp_path / f"{message.replace('/', '_')}.jpg"
    photo.write_bytes(b"jpg")
    turn = SimpleNamespace(tenant=tenant, state=state, now=now, send=send, sender=sender, message=message,
                           files=[photo], posted=[], gits=[])
    turn.post = lambda space, text, files: turn.posted.append((space, text, files))
    turn.git = lambda args: turn.gits.append(args)
    return turn


def call(turn, name, **args):
    (handler,) = [t.handler for t in reimbursement.tools(turn) if t.name == name]
    return asyncio.run(handler(args))


def text(result):
    return result["content"][0]["text"]


def invoice(turn, vendor="Bunnings", date="2026-11-03", amount=40.0, **extra):
    return call(turn, "record_reimbursement", vendor=vendor, invoice_date=date, amount=amount,
                currency="AUD", file=str(turn.files[0]), **extra)


def ledger_lines(path):
    lines = path.read_text().splitlines()
    return [dict(zip(lines[0].split("\t"), l.split("\t"))) for l in lines[1:]]


def live_ledger(tenant):
    return tenant / ".imperson" / "reimbursement" / "ledger.tsv"


def test_the_four_tools_are_named(tenant, tmp_path):
    names = [t.name for t in reimbursement.tools(make_turn(tenant, tmp_path))]
    assert names == ["allowance_left", "find_duplicate", "record_reimbursement", "record_not_reimbursed"]


def test_within_the_allowance_records_tasks_and_commits(tenant, tmp_path):
    turn = make_turn(tenant, tmp_path)
    result = invoice(turn)
    assert "within" in text(result) and "$60.00" in text(result) and "is_error" not in result
    (space, task, files), = turn.posted
    assert space == "spaces/DMS" and task.startswith("Reimburse Ann: Bunnings, 2026-11-03, $40.00 AUD")
    assert "Within the week's allowance" in task and files == [str(turn.files[0])]
    assert [r["outcome"] for r in ledger_lines(live_ledger(tenant))] == ["within"]
    add, commit = turn.gits
    assert add == ["-C", str(tenant), "add", str(live_ledger(tenant))]
    assert commit[:5] == ["-C", str(tenant), "commit", "-q", "-m"] and commit[-2:] == ["--", str(live_ledger(tenant))]
    assert "Chat message spaces/D1/messages/1" in commit[5]


def test_overdrawn_within_the_limit(tenant, tmp_path):
    turn = make_turn(tenant, tmp_path)
    assert "overdrawn" in text(invoice(turn, amount=110))
    assert "Overdraws the week's allowance by $10.00" in turn.posted[0][1]


def test_over_the_limit_leaves_the_payment_to_the_task_space(tenant, tmp_path):
    turn = make_turn(tenant, tmp_path)
    result = invoice(turn, amount=125)
    assert "over-limit" in text(result) and "task space to decide" in text(result)
    assert "Not reimbursed yet" in turn.posted[0][1] and "$25.00" in turn.posted[0][1]
    assert [r["outcome"] for r in ledger_lines(live_ledger(tenant))] == ["over-limit"]
    other = make_turn(tenant, tmp_path, message="spaces/D1/messages/2")
    assert "spent this week $0.00" in text(call(other, "allowance_left"))


def test_a_duplicate_is_refused_naming_the_earlier_line(tenant, tmp_path):
    invoice(make_turn(tenant, tmp_path))
    turn = make_turn(tenant, tmp_path, message="spaces/D2/messages/9", sender=BO)
    found = text(call(turn, "find_duplicate", invoice_date="2026-11-03", vendor="BUNNINGS ", amount=1))
    assert "Duplicate" in found and "Bunnings" in found
    assert "no" in text(call(turn, "find_duplicate", invoice_date="2026-11-04", vendor="Bunnings", amount=40)).lower()
    result = invoice(turn, vendor="BUNNINGS ")
    assert result["is_error"] and "duplicate" in text(result) and turn.posted == []
    assert len(ledger_lines(live_ledger(tenant))) == 1


def test_the_same_message_is_refused_twice(tenant, tmp_path):
    turn = make_turn(tenant, tmp_path)
    invoice(turn)
    again = invoice(turn, vendor="Officeworks", date="2026-11-04")
    assert again["is_error"] and "already recorded" in text(again)
    assert call(turn, "record_not_reimbursed", reason="x")["is_error"]
    assert len(turn.posted) == 1 and len(ledger_lines(live_ledger(tenant))) == 1


def test_a_file_from_another_message_is_refused(tenant, tmp_path):
    turn = make_turn(tenant, tmp_path)
    elsewhere = tmp_path / "other.jpg"
    elsewhere.write_bytes(b"x")
    result = call(turn, "record_reimbursement", vendor="Bunnings", invoice_date="2026-11-03", amount=40,
                  currency="AUD", file=str(elsewhere))
    assert result["is_error"] and "not one saved from this message" in text(result)
    assert not live_ledger(tenant).exists() and turn.posted == [] and turn.gits == []


def test_last_weeks_spending_does_not_count(tenant, tmp_path):
    last_week = make_turn(tenant, tmp_path, now=datetime(2026, 11, 1, 2, 0, tzinfo=timezone.utc))
    invoice(last_week, amount=90)
    turn = make_turn(tenant, tmp_path, message="spaces/D1/messages/2", now=MONDAY)
    assert "spent this week $0.00" in text(call(turn, "allowance_left"))
    assert "within" in text(invoice(turn, vendor="Officeworks", date="2026-11-02", amount=90))
    assert "week started 2026-11-02" in text(call(turn, "allowance_left"))


def test_allowance_left_counts_this_weeks_spending(tenant, tmp_path):
    invoice(make_turn(tenant, tmp_path), amount=30)
    answer = text(call(make_turn(tenant, tmp_path, message="spaces/D1/messages/2"), "allowance_left"))
    assert "allowance $100.00" in answer and "spent this week $30.00" in answer and "left $70.00" in answer
    assert "Overdraft limit $20.00" in answer


def test_a_sender_outside_the_team_has_none_unless_allowed_by_chat_id(tenant, tmp_path):
    turn = make_turn(tenant, tmp_path, sender=STRANGER)
    assert "weekly allowance $0.00" in text(call(turn, "allowance_left"))
    settings = tenant / ".imperson" / "reimbursement" / "settings.yaml"
    settings.write_text(settings.read_text().replace("allowances: {", f"allowances: {{{STRANGER}: 50, "))
    assert "weekly allowance $50.00" in text(call(make_turn(tenant, tmp_path, sender=STRANGER), "allowance_left"))


def test_off_writes_the_test_ledger_and_runs_no_git(tenant, tmp_path):
    turn = make_turn(tenant, tmp_path, send="off")
    invoice(turn)
    assert turn.gits == [] and not live_ledger(tenant).exists()
    assert len(ledger_lines(turn.state / "reimbursement-ledger-test.tsv")) == 1
    again = make_turn(tenant, tmp_path, send="test")
    assert "already recorded" in text(invoice(again))


def test_record_not_reimbursed_records_and_posts_nothing(tenant, tmp_path):
    turn = make_turn(tenant, tmp_path)
    result = call(turn, "record_not_reimbursed", vendor=None, reason="a screenshot")
    assert "is_error" not in result and turn.posted == []
    (row,) = ledger_lines(live_ledger(tenant))
    assert row["outcome"] == "rejected" and row["message"] == turn.message and row["employee"] == "Ann"
    assert len(turn.gits) == 2
    again = make_turn(tenant, tmp_path)
    assert call(again, "record_reimbursement", vendor="V", invoice_date="2026-11-03", amount=1,
                currency="AUD", file=str(again.files[0]))["is_error"]

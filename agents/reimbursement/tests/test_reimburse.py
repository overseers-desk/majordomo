"""One run of the agent over a stand-in for majordomo and a stand-in judgement."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

import reimburse

ME, ANN, BO, STRANGER = "users/ME", "users/ANN", "users/BO", "users/X"
NOW = datetime(2026, 11, 4, 2, 0, tzinfo=timezone.utc)      # Wednesday 12:00 in Brisbane
MONDAY = datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)   # Monday 10:00 in Brisbane


class FakeChat:
    """majordomo's surface as the agent calls it: DM spaces, each with posted files."""

    def __init__(self, posts):
        self.posts = posts            # [(space, message name, sender, content type, text)]
        self.sent = []

    def load_config(self, path):
        return {"me": {"user_id": ME}}

    def make_reader(self, cfg, source, **kw):
        chat = self

        class Reader:
            def spaces(self):
                return [{"space_name": sp, "space_type": "DIRECT_MESSAGE", "last_active": NOW}
                        for sp in sorted({p[0] for p in chat.posts})]
        return Reader()

    def attachments(self, cfg, *, space=None, message=None, start=None, download_to=None, cache_dir=None):
        rows = [{"message_name": m, "space_name": sp, "sender_name": who, "content_type": kind}
                for sp, m, who, kind, _ in self.posts if (space and sp == space) or m == message]
        if download_to:
            for r in rows:
                r["path"] = str(Path(download_to) / "invoice.jpg")
                Path(r["path"]).write_bytes(b"jpg")
        return rows

    def messages(self, cfg, space, *, start=None, source=None, cache_dir=None):
        return [{"name": m, "text": t} for sp, m, _, _, t in self.posts if sp == space]

    def send(self, cfg, *, to=None, space=None, text=None, attachments=None, cache_dir=None):
        self.sent.append({"to": to, "space": space, "text": text, "attachments": attachments})


def judged(vendor="Bunnings", date="2026-11-03", amount=40.0, qualified=True):
    return {"vendor": vendor, "invoice_date": date, "amount": amount, "currency": "AUD",
            "qualified": qualified, "reason": "Supplies for the stables.", "hint": None if qualified else "Fuel and feed qualify."}


@pytest.fixture
def tenant(tmp_path):
    root = tmp_path / "tenant"
    (root / ".reviewagent").mkdir(parents=True)
    (root / ".reimbursement").mkdir()
    (root / ".reviewagent" / "team.yaml").write_text(f"people:\n  Ann: {ANN}\n  Bo: {BO}\n")
    (root / ".reviewagent" / "settings.yaml").write_text("timezone: Australia/Brisbane\n")
    (root / ".reimbursement" / "settings.yaml").write_text(
        "task_space: spaces/DMS\nweek_starts: Monday\noverdraft_limit: 20\nallowances: {Ann: 100, Bo: 100}\n")
    (root / ".reimbursement" / "rubric.md").write_text("Supplies qualify.\n")
    return root


def run(tenant, tmp_path, posts, answers, *, send="live", now=NOW, chat=None, **extra):
    chat = chat or FakeChat(posts)
    answers = list(answers)
    git = []
    conf = {"majordomo_config": "unused", "tenant": str(tenant), "state_dir": str(tmp_path / "state"),
            "send": send, **extra}
    out = reimburse.Run(conf, judge=lambda *a: answers.pop(0), chat=chat,
                        git=lambda cmd, check: git.append(cmd), now=now).run()
    return chat, out, git


def ledger(tenant):
    lines = (tenant / ".reimbursement" / "ledger.tsv").read_text().splitlines()
    return [dict(zip(lines[0].split("\t"), l.split("\t"))) for l in lines[1:]]


def test_within_the_allowance_replies_tasks_records_and_commits(tenant, tmp_path):
    chat, _, git = run(tenant, tmp_path, [("spaces/D1", "spaces/D1/messages/1", ANN, "image/jpeg", "fuel")], [judged()])
    reply, task = chat.sent
    assert reply["to"] == ANN and "$60.00" in reply["text"]
    assert task["space"] == "spaces/DMS" and task["text"].startswith("Reimburse Ann: Bunnings") and task["attachments"]
    assert [r["outcome"] for r in ledger(tenant)] == ["within"]
    assert git[-1][:4] == ["git", "-C", str(tenant), "commit"]


def test_a_rejected_invoice_gets_the_hint_and_no_task(tenant, tmp_path):
    chat, _, _ = run(tenant, tmp_path, [("spaces/D1", "spaces/D1/messages/1", ANN, "image/png", "")], [judged(qualified=False)])
    (reply,) = chat.sent
    assert "does not qualify" in reply["text"] and "Fuel and feed qualify." in reply["text"]
    assert [r["outcome"] for r in ledger(tenant)] == ["rejected"]


def test_overdraft_within_the_limit_then_beyond_it(tenant, tmp_path):
    posts = [("spaces/D1", "spaces/D1/messages/1", ANN, "image/jpeg", ""),
             ("spaces/D1", "spaces/D1/messages/2", ANN, "application/pdf", "")]
    chat, _, _ = run(tenant, tmp_path, posts, [judged(amount=110), judged(vendor="Officeworks", date="2026-11-04", amount=15)])
    assert [r["outcome"] for r in ledger(tenant)] == ["overdrawn", "over-limit"]
    assert "overdrawn by $10.00" in chat.sent[0]["text"]
    assert "Not reimbursed yet" in chat.sent[-1]["text"]


def test_a_duplicate_names_the_earlier_invoice(tenant, tmp_path):
    posts = [("spaces/D1", "spaces/D1/messages/1", ANN, "image/jpeg", ""),
             ("spaces/D2", "spaces/D2/messages/9", BO, "image/jpeg", "")]
    chat, _, _ = run(tenant, tmp_path, posts, [judged(), judged(vendor="BUNNINGS ")])
    assert [r["outcome"] for r in ledger(tenant)] == ["within", "duplicate"]
    assert "already reimbursed" in chat.sent[-1]["text"]


def test_a_second_run_handles_nothing_twice(tenant, tmp_path):
    posts = [("spaces/D1", "spaces/D1/messages/1", ANN, "image/jpeg", "")]
    chat = FakeChat(posts)
    run(tenant, tmp_path, posts, [judged()], chat=chat)
    run(tenant, tmp_path, posts, [], chat=chat)
    assert len(ledger(tenant)) == 1 and len(chat.sent) == 2


def test_last_weeks_spending_does_not_count(tenant, tmp_path):
    posts = [("spaces/D1", "spaces/D1/messages/1", ANN, "image/jpeg", "")]
    run(tenant, tmp_path, posts, [judged(amount=90)], now=datetime(2026, 11, 1, 2, 0, tzinfo=timezone.utc))
    posts.append(("spaces/D1", "spaces/D1/messages/2", ANN, "image/jpeg", ""))
    chat, _, _ = run(tenant, tmp_path, posts, [judged(vendor="Officeworks", date="2026-11-02", amount=90)], now=MONDAY)
    assert [r["outcome"] for r in ledger(tenant)] == ["within", "within"]


def test_strangers_own_messages_and_other_files_are_ignored(tenant, tmp_path):
    posts = [("spaces/D1", "spaces/D1/messages/1", STRANGER, "image/jpeg", ""),
             ("spaces/D1", "spaces/D1/messages/2", ME, "image/jpeg", ""),
             ("spaces/D1", "spaces/D1/messages/3", ANN, "text/plain", "")]
    chat, _, _ = run(tenant, tmp_path, posts, [])
    assert chat.sent == []


def test_off_sends_nothing_and_keeps_the_tenant_ledger_clean(tenant, tmp_path):
    chat, out, git = run(tenant, tmp_path, [("spaces/D1", "spaces/D1/messages/1", ANN, "image/jpeg", "")], [judged()], send="off")
    assert chat.sent == [] and git == [] and len(out) == 2
    assert not (tenant / ".reimbursement" / "ledger.tsv").exists()
    assert (tmp_path / "state" / "ledger-test.tsv").exists()


def test_test_mode_reads_and_sends_only_the_correspondent(tenant, tmp_path):
    posts = [("spaces/D1", "spaces/D1/messages/1", ANN, "image/jpeg", ""),
             ("spaces/D2", "spaces/D2/messages/2", BO, "image/jpeg", "")]
    chat, _, git = run(tenant, tmp_path, posts, [judged()], send="test", test_correspondent=BO)
    assert {s["to"] for s in chat.sent} == {BO} and {s["space"] for s in chat.sent} == {None}
    assert len(chat.sent) == 2 and git == []


def test_a_failed_judgement_is_retried_next_run(tenant, tmp_path):
    posts = [("spaces/D1", "spaces/D1/messages/1", ANN, "image/jpeg", "")]
    chat = FakeChat(posts)

    def broken(*a):
        raise ValueError("no JSON")
    reimburse.Run({"majordomo_config": "x", "tenant": str(tenant), "state_dir": str(tmp_path / "state"), "send": "live"},
                  judge=broken, chat=chat, git=lambda *a, **k: None, now=NOW).run()
    assert chat.sent == []
    run(tenant, tmp_path, posts, [judged()], chat=chat)
    assert [r["outcome"] for r in ledger(tenant)] == ["within"]

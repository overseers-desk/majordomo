"""The run over a stand-in for majordomo and a stand-in for the Agent SDK conversation."""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import imperson

ME, ANN, BO = "users/ME", "users/ANN", "users/BO"
NOW = datetime(2026, 11, 4, 2, 0, tzinfo=timezone.utc)


class FakeChat:
    """majordomo as imperson calls it: direct messages, each message optionally with one file."""

    def __init__(self, posts):
        self.posts = posts            # [(space, message name, sender, text, has_file)]
        self.sent = []
        self.last_active = NOW

    def load_config(self, path):
        return {"me": {"user_id": ME}}

    def make_reader(self, cfg, source, **kw):
        spaces, last = sorted({p[0] for p in self.posts}), self.last_active

        class Reader:
            def spaces(self):
                return [{"space_name": s, "space_type": "DIRECT_MESSAGE", "last_active": last} for s in spaces]
        return Reader()

    def messages(self, cfg, space, *, start=None, source=None, cache_dir=None):
        return [{"name": m, "sender_name": who, "sender_display": who[6:].title(), "text": text,
                 "create_time": datetime(2026, 11, 4, 1, i)}
                for i, (sp, m, who, text, _) in enumerate(self.posts) if sp == space]

    def attachments(self, cfg, *, space=None, message=None, start=None, download_to=None, cache_dir=None):
        rows = [{"message_name": m} for sp, m, _, _, f in self.posts if f and (sp == space or m == message)]
        if download_to:
            for r in rows:
                r["path"] = str(Path(download_to) / "invoice.jpg")
                Path(r["path"]).write_bytes(b"jpg")
        return rows

    def send(self, cfg, *, to=None, space=None, text=None, attachments=None, cache_dir=None):
        self.sent.append({"to": to, "space": space, "text": text, "attachments": attachments})


class Talk:
    """Records each turn, and answers through the reply tool unless told to stay silent."""

    def __init__(self, answer=lambda prompt: f"re: {prompt.splitlines()[1]}"):
        self.turns = []
        self.answer = answer

    def __call__(self, prompt, *, session, cwd, system_prompt, skills, tools, read_dirs):
        self.turns.append({"prompt": prompt, "session": session, "system": system_prompt,
                           "tools": [t.name for t in tools]})
        text = self.answer(prompt)
        if text is not None:
            reply = next(t for t in tools if t.name == "reply")
            asyncio.run(reply.handler({"text": text}))
        return session or f"session-{len(self.turns)}"


def account(tmp_path, posts, talk, *, chat=None, **table):
    chat = chat or FakeChat(posts)
    tenant = tmp_path / "tenant"
    tenant.mkdir(exist_ok=True)
    conf = {"majordomo_config": "unused", "tenant": str(tenant), "send": "live", "skills": ["weather"], **table}
    return imperson.Account("admin", conf, chat=chat, talk=talk, git=lambda a: None, now=NOW,
                            state_root=tmp_path / "state"), chat


def test_every_message_reaches_its_session_and_is_answered(tmp_path):
    posts = [("spaces/D1", "spaces/D1/messages/1", ANN, "Hi", False),
             ("spaces/D1", "spaces/D1/messages/2", ANN, "Will it rain tomorrow?", False),
             ("spaces/D2", "spaces/D2/messages/3", BO, "Here is my receipt", True)]
    talk = Talk()
    acct, chat = account(tmp_path, posts, talk)
    acct.run()
    assert [t["prompt"].splitlines()[1] for t in talk.turns] == ["Hi", "Will it rain tomorrow?", "Here is my receipt"]
    assert "File saved from this message:" in talk.turns[2]["prompt"]
    assert [(s["to"], s["text"]) for s in chat.sent] == [(ANN, "re: Hi"), (ANN, "re: Will it rain tomorrow?"),
                                                         (BO, "re: Here is my receipt")]
    assert "Answer every message." in talk.turns[0]["system"]


def test_one_session_per_conversation_resumed_on_the_next_run(tmp_path):
    posts = [("spaces/D1", "spaces/D1/messages/1", ANN, "Hi", False)]
    talk = Talk()
    acct, chat = account(tmp_path, posts, talk)
    acct.run()
    posts.append(("spaces/D1", "spaces/D1/messages/2", ANN, "And Saturday?", False))
    chat.last_active = later = NOW + timedelta(minutes=5)
    imperson.Account("admin", acct.table, chat=chat, talk=talk, git=lambda a: None, now=later,
                     state_root=tmp_path / "state").run()
    assert [t["session"] for t in talk.turns] == [None, "session-1"]
    assert len(talk.turns) == 2


def test_the_rule_lets_the_session_stay_silent(tmp_path):
    posts = [("spaces/D1", "spaces/D1/messages/1", ANN, "lunch?", False),
             ("spaces/D1", "spaces/D1/messages/2", ANN, "what's on this weekend?", False)]
    talk = Talk(answer=lambda p: "Markets on Saturday." if "on this weekend" in p else None)
    acct, chat = account(tmp_path, posts, talk, speak_when="Speak only when asked about events.")
    acct.run()
    assert len(talk.turns) == 2
    assert [s["text"] for s in chat.sent] == ["Markets on Saturday."]
    assert "Speak only when asked about events." in talk.turns[0]["system"]


def test_own_messages_are_not_handed_to_the_session(tmp_path):
    talk = Talk()
    acct, chat = account(tmp_path, [("spaces/D1", "spaces/D1/messages/1", ME, "sent earlier", False)], talk)
    acct.run()
    assert talk.turns == []


def test_a_failed_turn_is_tried_again_and_nothing_is_handled_twice(tmp_path):
    posts = [("spaces/D1", "spaces/D1/messages/1", ANN, "Hi", False),
             ("spaces/D1", "spaces/D1/messages/2", ANN, "still there?", False)]
    calls = []

    def flaky(prompt, **kw):
        calls.append(prompt.splitlines()[1])
        if len(calls) == 2:
            raise RuntimeError("the turn ended: error_max_turns")
        return "s"
    acct, chat = account(tmp_path, posts, flaky)
    acct.run()
    imperson.Account("admin", acct.table, chat=chat, talk=flaky, git=lambda a: None, now=NOW,
                     state_root=tmp_path / "state").run()
    assert calls == ["Hi", "still there?", "still there?"]


@pytest.mark.parametrize("mode", ["off", "test"])
def test_the_send_gate(tmp_path, mode):
    posts = [("spaces/D1", "spaces/D1/messages/1", ANN, "Hi", False),
             ("spaces/D2", "spaces/D2/messages/2", BO, "Hi", False)]
    talk = Talk()
    acct, chat = account(tmp_path, posts, talk, send=mode, test_correspondent=BO)
    out = acct.run()
    if mode == "off":
        assert chat.sent == [] and len(out) == 2
    else:
        assert len(talk.turns) == 1 and [s["to"] for s in chat.sent] == [BO]


def test_a_skill_with_missing_files_leaves_the_conversation_running(tmp_path):
    talk = Talk()
    acct, chat = account(tmp_path, [("spaces/D1", "spaces/D1/messages/1", ANN, "Hi", False)], talk,
                         skills=["reimbursement"])
    acct.run()
    assert talk.turns[0]["tools"] == ["reply"] and len(chat.sent) == 1


def test_skills_bring_their_tools(tmp_path):
    tenant = tmp_path / "tenant"
    (tenant / ".reviewagent").mkdir(parents=True)
    (tenant / ".imperson" / "reimbursement").mkdir(parents=True)
    (tenant / ".reviewagent" / "team.yaml").write_text(f"people:\n  Ann: {ANN}\n")
    (tenant / ".reviewagent" / "settings.yaml").write_text("timezone: Australia/Brisbane\n")
    (tenant / ".imperson" / "reimbursement" / "settings.yaml").write_text(
        "task_space: spaces/T\nweek_starts: Monday\noverdraft_limit: 0\nallowances: {}\n")
    talk = Talk()
    acct, _ = account(tmp_path, [("spaces/D1", "spaces/D1/messages/1", ANN, "Hi", False)], talk,
                      skills=["reimbursement", "weather", "events"])
    acct.run()
    assert {"reply", "allowance_left", "record_reimbursement"} <= set(talk.turns[0]["tools"])

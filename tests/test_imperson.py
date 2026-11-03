"""The run over a stand-in for majordomo and a stand-in for the Agent SDK conversation."""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import _shim  # noqa: F401
from imperson import agent as imperson

ME, ANN, BO = "users/ME", "users/ANN", "users/BO"
NOW = datetime(2026, 11, 4, 2, 0, tzinfo=timezone.utc)


class FakeChat:
    """majordomo as imperson calls it: direct messages, each message optionally with one file."""

    def __init__(self, posts):
        self.posts = posts            # [(space, message name, sender, text, file)]: file True, False or "unfetchable"
        self.sent = []
        self.last_active = NOW

    def load_config(self, settings):
        return settings

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
        rows = []
        for i, (sp, m, who, _, f) in enumerate(self.posts):
            if f and (sp == space or m == message):
                rows.append({"attachment_name": f"{m}/attachments/A", "message_name": m, "sender_name": who,
                             "create_time": datetime(2026, 11, 4, 1, i, tzinfo=timezone.utc),
                             "content_name": "invoice.jpg", "content_type": "image/jpeg"})
                if download_to and f == "unfetchable":
                    rows[-1]["error"] = "majordomo: invoice.jpg: HTTP 500"
                elif download_to:
                    rows[-1]["path"] = str(Path(download_to) / "invoice.jpg")
                    Path(rows[-1]["path"]).write_bytes(b"jpg")
        return rows

    def send(self, cfg, *, to=None, space=None, text=None, attachments=None, cache_dir=None):
        self.sent.append({"to": to, "space": space, "text": text, "attachments": attachments})


class Talk:
    """Records each turn, and answers through the reply tool unless told to stay silent."""

    def __init__(self, answer=lambda prompt: f"re: {prompt.splitlines()[1]}"):
        self.turns = []
        self.answer = answer

    def __call__(self, prompt, *, session, cwd, system_prompt, skills, tools, read_dirs, env):
        self.turns.append({"prompt": prompt, "session": session, "system": system_prompt,
                           "tools": [t.name for t in tools]})
        text = self.answer(prompt)
        if text is not None:
            reply = next(t for t in tools if t.name == "reply")
            asyncio.run(reply.handler({"text": text}))
        return session or f"session-{len(self.turns)}"


@pytest.fixture(autouse=True)
def books_for_accounts(books):
    """Every test here builds an Account, and hands it the test server's books whatever prefix it asks for."""
    account.books, account.prefixes = books, []


def account(tmp_path, posts, talk, *, chat=None, now=NOW, fetch=lambda *a, **kw: [], **table):
    chat = chat or FakeChat(posts)
    files = tmp_path / "tenant"
    files.mkdir(exist_ok=True)
    tenants = {"business": imperson.Tenant("business", "example/business", "token", files, fetch=fetch)}
    conf = {"keys": str(tmp_path / "keys"), "tenant": "business", "send": "live", "skills": ["weather"], **table}
    return imperson.Account("admin", conf, tenants=tenants, books=lambda db, prefix: account.prefixes.append(prefix) or account.books,
                            chat=chat, talk=talk, me=lambda cfg: ME, now=now, state_root=tmp_path / "state"), chat


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
    assert "read its files again" in talk.turns[0]["system"]
    assert account.prefixes == ["imperson_"]


def test_each_file_reaches_the_conversation_as_an_upload_in_the_books(tmp_path, books):
    posts = [("spaces/D1", "spaces/D1/messages/1", ANN, "receipt", True),
             ("spaces/D1", "spaces/D1/messages/2", ANN, "the same receipt", True),
             ("spaces/D1", "spaces/D1/messages/3", ANN, "and this one", "unfetchable")]
    talk = Talk()
    acct, chat = account(tmp_path, posts, talk)
    acct.run()
    saved = acct.state / "work" / "spaces_D1_messages_1" / "invoice.jpg"
    assert [t["prompt"].splitlines()[2] for t in talk.turns] == [
        f"File saved from this message: {saved} (upload 1, image/jpeg)",
        f"File saved from this message: {str(saved).replace('_1', '_2')} (upload 2, image/jpeg; the same file as "
        "upload 1, received 2026-11-04T01:00:00+00:00 in message spaces/D1/messages/1)",
        "A file in this message could not be fetched: invoice.jpg (majordomo: invoice.jpg: HTTP 500) (upload 3)"]
    assert [(u["sender"], u["message"], u["name"]) for u in books.uploads([1, 2, 3])] == [
        (ANN, f"spaces/D1/messages/{n}", "invoice.jpg") for n in (1, 2, 3)]
    assert len(chat.sent) == 3


def test_a_file_too_large_for_the_books_reaches_the_conversation_by_its_path_and_goes_out_with_its_task(tmp_path, books,
                                                                                                          monkeypatch):
    import yaml
    from test_imperson_ledger import WEEKLY
    monkeypatch.setattr(books, "packet", 0)
    tenant = tmp_path / "tenant"
    (tenant / ".imperson" / "spend").mkdir(parents=True)
    (tenant / "team.yaml").write_text(f"people:\n  Ann: {ANN}\n")
    (tenant / ".imperson" / "spend" / "policy.yaml").write_text(yaml.safe_dump(WEEKLY))
    prompts = []

    def talk(prompt, *, tools, **kw):
        prompts.append(prompt)
        (record,) = [t for t in tools if t.name == "spend_record"]
        asyncio.run(record.handler({"claim": {"vendor": "Bunnings", "invoice_date": "2026-11-03", "amount": 10},
                                    "scope": "in", "uploads": [1]}))
        return "s"
    acct, chat = account(tmp_path, [("spaces/D1", "spaces/D1/messages/1", ANN, "receipt", True)], talk, skills=["spend"])
    acct.run()
    saved = acct.state / "work" / "spaces_D1_messages_1" / "invoice.jpg"
    line = prompts[0].splitlines()[2]
    assert line.startswith(f"File saved from this message: {saved} (upload 1, image/jpeg; not kept in the books: the "
                           "file of 3 bytes, sent as ") and line.endswith("exceeds the server's limit of 0)")
    assert chat.sent == [{"to": None, "space": "spaces/T", "text": "Pay Ann 10.00, 90.00 left.", "attachments": [str(saved)]}]


def test_a_message_whose_files_cannot_reach_the_books_waits_for_the_next_run(tmp_path, books, monkeypatch):
    monkeypatch.setattr(books, "add_upload", lambda **kw: (_ for _ in ()).throw(RuntimeError("server has gone away")))
    talk = Talk()
    acct, chat = account(tmp_path, [("spaces/D1", "spaces/D1/messages/1", ANN, "receipt", True)], talk)
    acct.run()
    assert talk.turns == [] and chat.sent == [] and acct.saved["mark"] is None


def test_the_tenant_is_refreshed_once_a_run_and_only_when_there_is_a_message(tmp_path):
    fetched, posts = [], [("spaces/D1", "spaces/D1/messages/1", ANN, "Hi", False), ("spaces/D2", "spaces/D2/messages/2", BO, "Hi", False)]
    acct, chat = account(tmp_path, posts, Talk(), fetch=lambda *a, **kw: fetched.append(a) or [])
    acct.run()
    account(tmp_path, posts, Talk(), chat=chat, now=NOW + timedelta(minutes=5), fetch=acct.tenant.fetch)[0].run()   # nothing new
    assert fetched == [("example/business", "token", tmp_path / "tenant")]


def test_with_no_cache_a_failed_fetch_stops_the_account_and_keeps_the_mark(tmp_path):
    def down(*a, **kw):
        raise RuntimeError("GitHub answered 503")
    talk = Talk()
    acct, _ = account(tmp_path, [("spaces/D1", "spaces/D1/messages/1", ANN, "Hi", False)], talk, fetch=down)
    with pytest.raises(RuntimeError, match="could not be fetched and none are cached"):
        acct.run()
    assert talk.turns == [] and not (acct.state / "state.json").exists()
    (acct.tenant.files / "tenant.json").write_text("{}")                  # a cache an earlier run left
    acct.run()
    assert len(talk.turns) == 1 and acct.saved["mark"] == NOW.isoformat()


def test_one_session_per_conversation_resumed_on_the_next_run(tmp_path):
    posts = [("spaces/D1", "spaces/D1/messages/1", ANN, "Hi", False)]
    talk = Talk()
    acct, chat = account(tmp_path, posts, talk)
    acct.run()
    posts.append(("spaces/D1", "spaces/D1/messages/2", ANN, "And Saturday?", False))
    chat.last_active = later = NOW + timedelta(minutes=5)
    account(tmp_path, posts, talk, chat=chat, now=later)[0].run()
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


def test_the_account_is_the_token_s_own_person_and_names_only_its_key_folder(tmp_path):
    acct, _ = account(tmp_path, [], Talk())
    assert acct.me == ME
    assert acct.cfg == {"api": {"token_file": str(tmp_path / "keys" / "token.json"),
                                "client_file": str(tmp_path / "keys" / "client_secret.json")}}


def test_no_config_toml_is_ever_opened(tmp_path, monkeypatch):
    import builtins
    import majordomo

    real_open = builtins.open

    def guarded(file, *a, **kw):
        assert not str(file).endswith("config.toml"), f"opened {file}"
        return real_open(file, *a, **kw)
    monkeypatch.setattr(builtins, "open", guarded)
    monkeypatch.setattr(majordomo.config, "config_toml", lambda: pytest.fail("looked for majordomo's config.toml"))
    chat = FakeChat([("spaces/D1", "spaces/D1/messages/1", ANN, "Hi", False)])
    chat.load_config = majordomo.load_config
    acct, _ = account(tmp_path, [], Talk(), chat=chat)
    acct.run()
    assert acct.cfg["api"]["token_file"].endswith("token.json")


def test_the_session_sees_imperson_s_skills_and_the_business_s_own(tmp_path):
    acct, _ = account(tmp_path, [], Talk())
    own = acct.tenant.files / ".imperson" / "skills" / "reimbursement"
    own.mkdir(parents=True)
    (own / "SKILL.md").write_text("---\nname: reimbursement\n---\n")
    folder = acct.state / "workspace" / ".claude" / "skills"
    folder.parent.mkdir(parents=True)
    folder.symlink_to(tmp_path / "gone")                      # a link an older layout left
    acct.workspace()
    links = {p.name: p.readlink() for p in folder.iterdir()}
    assert links["reimbursement"] == own and links["weather"] == imperson.HERE / "skills" / "weather"


def test_a_resumed_conversation_is_told_which_rule_files_changed(tmp_path):
    posts = [("spaces/D1", "spaces/D1/messages/1", ANN, "receipt", False)]
    talk = Talk()
    acct, chat = account(tmp_path, posts, talk)
    rubric = acct.tenant.files / ".imperson" / "reimbursement" / "rubric.md"
    rubric.parent.mkdir(parents=True)
    rubric.write_text("Supplies qualify.")
    acct.run()
    rubric.write_text("Supplies qualify. Prepared food does not.")
    posts.append(("spaces/D1", "spaces/D1/messages/2", ANN, "same receipt again", False))
    chat.last_active = later = NOW + timedelta(minutes=5)
    account(tmp_path, posts, talk, chat=chat, now=later)[0].run()
    assert "changed since this conversation last read them" not in talk.turns[0]["prompt"]
    assert str(rubric) in talk.turns[1]["prompt"].split("\n\n")[0]


def test_a_resumed_conversation_is_told_the_overview_changed_and_not_the_cache_s_own_record(tmp_path):
    posts = [("spaces/D1", "spaces/D1/messages/1", ANN, "Hi", False)]
    talk = Talk()
    acct, chat = account(tmp_path, posts, talk)
    overview, record = acct.tenant.files / "business-overview.md", acct.tenant.files / "tenant.json"
    overview.write_text("A riding school.")
    record.write_text('{"commit": "c1"}')
    acct.run()
    overview.write_text("A riding school with a cafe.")
    record.write_text('{"commit": "c2"}')
    posts.append(("spaces/D1", "spaces/D1/messages/2", ANN, "Open on Sunday?", False))
    chat.last_active = later = NOW + timedelta(minutes=5)
    account(tmp_path, posts, talk, chat=chat, now=later)[0].run()
    assert talk.turns[1]["prompt"].split("\n\n")[0].splitlines()[1:] == [str(overview)]


def test_claude_code_runs_in_the_account_s_own_configuration_folder(tmp_path):
    acct, _ = account(tmp_path, [], Talk())
    assert acct.env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "state" / "admin" / "claude")


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
    account(tmp_path, posts, flaky, chat=chat)[0].run()
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
    assert account.prefixes == ["imperson_test_"]


def test_a_skill_with_missing_files_leaves_the_conversation_running(tmp_path):
    talk = Talk()
    acct, chat = account(tmp_path, [("spaces/D1", "spaces/D1/messages/1", ANN, "Hi", False)], talk,
                         skills=["spend"])
    acct.run()
    assert talk.turns[0]["tools"] == ["reply"] and len(chat.sent) == 1


def test_a_skill_with_a_policy_brings_the_general_tools_named_for_it(tmp_path):
    import yaml
    from test_imperson_ledger import WEEKLY
    tenant = tmp_path / "tenant"
    (tenant / ".imperson" / "spend").mkdir(parents=True)
    (tenant / "team.yaml").write_text(f"people:\n  Ann: {ANN}\n")
    (tenant / ".imperson" / "spend" / "policy.yaml").write_text(yaml.safe_dump(WEEKLY))
    talk = Talk()
    acct, _ = account(tmp_path, [("spaces/D1", "spaces/D1/messages/1", ANN, "Hi", False)], talk,
                      skills=["spend", "weather", "events"])
    acct.run()
    assert set(talk.turns[0]["tools"]) == {"reply", "spend_balance", "spend_find_match", "spend_record", "spend_order",
                                           "spend_report"}

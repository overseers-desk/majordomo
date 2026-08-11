"""api.attachments with an injected fake Chat service (no creds/network): the
listing shape off a message and off a space, the download (bytes written, path
reported), and the refusals. Those are a blocked space worded as not-found, a
Drive-backed file, an existing target, a filename that would escape the
directory, a missing destination, and a wrong target count. Plus the
WORLD_AS_OF bound on a read.
"""

import _shim  # noqa: F401

import types
from unittest.mock import MagicMock

import pytest

from majordomo import api

SPACE = "spaces/OK"
MSG = "spaces/OK/messages/T.1"


def _att(content_name="clip.mp4", *, name="spaces/OK/messages/T.1/attachments/A1",
         source=api.UPLOADED, resource="res-1", content_type="video/mp4"):
    att = {"name": name, "contentName": content_name, "contentType": content_type,
           "source": source}
    if resource is not None:
        att["attachmentDataRef"] = {"resourceName": resource}
    return att


def _msg(*attachments, name=MSG, create_time="2026-06-24T00:04:54.613087Z"):
    return {"name": name, "createTime": create_time,
            "sender": {"name": "users/sam", "type": "HUMAN"},
            "text": "please find attached", "attachment": list(attachments)}


def _chat(message=None, listed=None, spaces=None):
    """A fake Chat service answering messages.get, messages.list and spaces.list."""
    chat = MagicMock()
    chat.spaces().messages().get.return_value.execute.return_value = message or _msg()
    chat.spaces().messages().list.return_value.execute.return_value = {
        "messages": listed if listed is not None else []}
    chat.spaces().list.return_value.execute.return_value = {
        "spaces": spaces if spaces is not None else [
            {"name": SPACE, "displayName": "Marketing"}]}
    return chat


def _fake_download(monkeypatch, payload=b"BYTES"):
    """Stand in for MediaIoBaseDownload so no google libs and no network are
    needed: it writes the payload into the caller's buffer in one chunk."""
    class _D:
        def __init__(self, fh, _request):
            self.fh = fh

        def next_chunk(self):
            self.fh.write(payload)
            return None, True

    monkeypatch.setattr(api, "_media_download", lambda: _D)


# --- listing -------------------------------------------------------------

def test_message_scope_lists_its_files():
    chat = _chat(_msg(_att("a.mp4"), _att("b.mp4", name="…/A2", resource="res-2")))
    rows = api.attachments({}, [], message=MSG, service=chat)
    assert [r["content_name"] for r in rows] == ["a.mp4", "b.mp4"]
    assert [r["resource_name"] for r in rows] == ["res-1", "res-2"]
    assert rows[0]["sender_name"] == "users/sam"
    assert rows[0]["space_name"] == SPACE
    assert rows[0]["message_name"] == MSG
    # One get, not a paged read of the whole space.
    chat.spaces().messages().list.assert_not_called()


def test_message_without_files_reports_nothing():
    chat = _chat({"name": MSG, "createTime": "2026-06-24T00:00:00Z"})
    assert api.attachments({}, [], message=MSG, service=chat) == []


def test_space_scope_flattens_files_across_messages():
    listed = [
        _msg(_att("one.jpeg", content_type="image/jpeg"), name="spaces/OK/messages/A.1"),
        {"name": "spaces/OK/messages/B.1", "createTime": "2026-06-24T01:00:00Z"},
        _msg(_att("two.mp4"), name="spaces/OK/messages/C.1"),
    ]
    chat = _chat(listed=listed)
    rows = api.attachments({}, [], space=SPACE, service=chat)
    assert [r["content_name"] for r in rows] == ["one.jpeg", "two.mp4"]
    assert rows[0]["space_display"] == "Marketing"
    # The list response already carries the files; no per-message fetch.
    chat.spaces().messages().get.assert_not_called()


def test_thread_scope_keeps_only_that_thread():
    listed = [_msg(_att("keep.mp4"), name="spaces/OK/messages/T.1"),
              _msg(_att("drop.mp4"), name="spaces/OK/messages/OTHER.1")]
    rows = api.attachments({}, [], thread="spaces/OK/messages/T.1", service=_chat(listed=listed))
    assert [r["content_name"] for r in rows] == ["keep.mp4"]


def test_limit_caps_rows():
    chat = _chat(_msg(_att("a"), _att("b"), _att("c")))
    assert len(api.attachments({}, [], message=MSG, limit=2, service=chat)) == 2


# --- download ------------------------------------------------------------

def test_download_writes_the_file_and_reports_the_path(tmp_path, monkeypatch):
    _fake_download(monkeypatch, b"VIDEO")
    chat = _chat(_msg(_att("clip.mp4")))
    rows = api.attachments({}, [], message=MSG, download_to=str(tmp_path), service=chat)
    written = tmp_path / "clip.mp4"
    assert written.read_bytes() == b"VIDEO"
    assert rows[0]["path"] == str(written)
    _, kw = chat.media().download_media.call_args
    assert kw["resourceName"] == "res-1"


def test_listing_without_download_writes_nothing(tmp_path):
    chat = _chat(_msg(_att("clip.mp4")))
    rows = api.attachments({}, [], message=MSG, service=chat)
    assert "path" not in rows[0]
    assert list(tmp_path.iterdir()) == []
    chat.media().download_media.assert_not_called()


def test_download_refuses_to_overwrite(tmp_path, monkeypatch):
    _fake_download(monkeypatch)
    (tmp_path / "clip.mp4").write_bytes(b"MINE")
    chat = _chat(_msg(_att("clip.mp4")))
    with pytest.raises(SystemExit) as ei:
        api.attachments({}, [], message=MSG, download_to=str(tmp_path), service=chat)
    assert "already exists" in str(ei.value)
    assert (tmp_path / "clip.mp4").read_bytes() == b"MINE"


def test_download_refuses_a_drive_backed_file(tmp_path, monkeypatch):
    _fake_download(monkeypatch)
    chat = _chat(_msg(_att("doc.pdf", source="DRIVE_FILE", resource=None)))
    with pytest.raises(SystemExit) as ei:
        api.attachments({}, [], message=MSG, download_to=str(tmp_path), service=chat)
    assert "Drive" in str(ei.value)
    assert "doc.pdf" in str(ei.value)


def test_drive_backed_file_still_lists():
    chat = _chat(_msg(_att("doc.pdf", source="DRIVE_FILE", resource=None)))
    rows = api.attachments({}, [], message=MSG, service=chat)
    assert rows[0]["source"] == "DRIVE_FILE"
    assert rows[0]["resource_name"] is None


def test_a_traversing_filename_cannot_escape_the_directory(tmp_path, monkeypatch):
    _fake_download(monkeypatch)
    chat = _chat(_msg(_att("../escaped.mp4")))
    rows = api.attachments({}, [], message=MSG, download_to=str(tmp_path), service=chat)
    assert rows[0]["path"] == str(tmp_path / "escaped.mp4")
    assert (tmp_path / "escaped.mp4").exists()
    assert not (tmp_path.parent / "escaped.mp4").exists()


def test_an_all_path_filename_fails_loud(tmp_path, monkeypatch):
    _fake_download(monkeypatch)
    chat = _chat(_msg(_att("../")))
    with pytest.raises(SystemExit) as ei:
        api.attachments({}, [], message=MSG, download_to=str(tmp_path), service=chat)
    assert "no usable filename" in str(ei.value)


def test_missing_destination_directory_fails_before_any_call(tmp_path):
    chat = _chat(_msg(_att("clip.mp4")))
    with pytest.raises(SystemExit) as ei:
        api.attachments({}, [], message=MSG, download_to=str(tmp_path / "nope"), service=chat)
    assert "no such directory" in str(ei.value)
    chat.spaces().messages().get.assert_not_called()


# --- refusals ------------------------------------------------------------

def test_blocked_space_worded_as_not_found():
    chat = _chat()
    with pytest.raises(SystemExit) as ei:
        api.attachments({}, [SPACE], space=SPACE, service=chat)
    assert str(ei.value) == f"majordomo: {SPACE}: not found."
    chat.spaces().messages().list.assert_not_called()


def test_blocked_space_refused_through_a_message_name_too():
    chat = _chat()
    with pytest.raises(SystemExit) as ei:
        api.attachments({}, [SPACE], message=MSG, service=chat)
    assert str(ei.value) == f"majordomo: {SPACE}: not found."
    chat.spaces().messages().get.assert_not_called()


def test_absent_message_uses_the_same_wording():
    chat = _chat()
    err = Exception("404")
    err.resp = types.SimpleNamespace(status=404)
    chat.spaces().messages().get.return_value.execute.side_effect = err
    with pytest.raises(SystemExit) as ei:
        api.attachments({}, [], message=MSG, service=chat)
    assert str(ei.value) == f"majordomo: {MSG}: not found."


def test_non_404_api_errors_stay_loud():
    chat = _chat()
    err = Exception("403")
    err.resp = types.SimpleNamespace(status=403)
    chat.spaces().messages().get.return_value.execute.side_effect = err
    with pytest.raises(Exception, match="403"):
        api.attachments({}, [], message=MSG, service=chat)


def test_needs_exactly_one_scope():
    with pytest.raises(SystemExit):
        api.attachments({}, [], service=_chat())
    with pytest.raises(SystemExit):
        api.attachments({}, [], space=SPACE, message=MSG, service=_chat())


# --- the replay bound ----------------------------------------------------

def test_world_as_of_hides_a_file_posted_after_the_bound(monkeypatch):
    monkeypatch.setenv(api.config.WORLD_AS_OF_ENV, "2026-06-01T00:00:00+00:00")
    chat = _chat(_msg(_att("later.mp4")))  # posted 2026-06-24, past the bound
    assert api.attachments({}, [], message=MSG, service=chat) == []


def test_world_as_of_keeps_a_file_posted_before_the_bound(monkeypatch):
    monkeypatch.setenv(api.config.WORLD_AS_OF_ENV, "2026-07-01T00:00:00+00:00")
    chat = _chat(_msg(_att("earlier.mp4")))
    rows = api.attachments({}, [], message=MSG, service=chat)
    assert [r["content_name"] for r in rows] == ["earlier.mp4"]


def test_download_is_not_refused_under_a_bound(tmp_path, monkeypatch):
    """A read is bounded, not refused (unlike send): a file that existed before
    the bound is legitimately part of the replay."""
    monkeypatch.setenv(api.config.WORLD_AS_OF_ENV, "2026-07-01T00:00:00+00:00")
    _fake_download(monkeypatch, b"OLD")
    chat = _chat(_msg(_att("clip.mp4")))
    rows = api.attachments({}, [], message=MSG, download_to=str(tmp_path), service=chat)
    assert (tmp_path / "clip.mp4").read_bytes() == b"OLD"
    assert rows[0]["path"] == str(tmp_path / "clip.mp4")

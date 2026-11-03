"""The business's files fetched from GitHub through the API, against a stand-in for the API."""

import _shim  # noqa: F401

import hashlib
import io
import json
import urllib.error

import pytest

from imperson import tenant

REPO = "example/business"
POLICY = b"team: .team/people.yaml\nperiod: {every: week}\n"
FILES = {
    "business-overview.md": b"A riding school.\n",
    "README.md": b"not wanted\n",
    ".imperson/spend/policy.yaml": POLICY,
    ".imperson/spend/rubric.md": b"# Rubric\n",
    ".imperson/skills/spend/SKILL.md": b"---\nname: spend\n---\n",
    ".team/people.yaml": b"people: {Ann: users/ANN}\n",
    ".team/cases/one.md": b"not wanted\n",
}


class Response(io.BytesIO):
    def __init__(self, body, etag=None):
        super().__init__(body)
        self.status, self.headers = 200, {"ETag": etag} if etag else {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class FakeGitHub:
    """A repository as the API serves it: a branch head, trees by sha, file contents by path."""

    def __init__(self, files, branch="main", commit="c1"):
        self.files, self.branch, self.commit = dict(files), branch, commit
        self.calls = []

    def blob(self, path):
        return hashlib.sha1(self.files[path]).hexdigest()

    def tree(self, sha, recursive):
        folder = "" if sha == "root" else sha + "/"
        inside = {p[len(folder):] for p in self.files if p.startswith(folder)}
        if recursive:
            return [{"path": p, "type": "blob", "sha": self.blob(folder + p)} for p in sorted(inside)]
        entries = []
        for name in sorted({p.split("/")[0] for p in inside}):
            if folder + name in self.files:
                entries.append({"path": name, "type": "blob", "sha": self.blob(folder + name)})
            else:
                entries.append({"path": name, "type": "tree", "sha": folder + name})
        return entries

    def __call__(self, req):
        url = req.full_url
        self.calls.append(url)
        if req.get_header("Authorization") != "Bearer tok":
            raise urllib.error.HTTPError(url, 401, "Bad credentials", {}, None)
        path = url.removeprefix(f"{tenant.API}/repos/{REPO}")
        if path == "":
            return Response(json.dumps({"default_branch": self.branch}).encode())
        if path == f"/branches/{self.branch}":
            if req.get_header("If-none-match") == f'"{self.commit}"':
                raise urllib.error.HTTPError(url, 304, "Not Modified", {}, None)
            return Response(json.dumps({"commit": {"sha": self.commit, "commit": {"tree": {"sha": "root"}}}}).encode(),
                            etag=f'"{self.commit}"')
        if path.startswith("/git/trees/"):
            sha, _, query = path[len("/git/trees/"):].partition("?")
            return Response(json.dumps({"tree": self.tree(sha, query == "recursive=1"), "truncated": False}).encode())
        if path.startswith("/contents/"):
            p = path[len("/contents/"):].partition("?")[0]
            return Response(self.files[p])
        raise AssertionError(url)


def fetched(github):
    return sorted(c.split("/contents/")[1].partition("?")[0] for c in github.calls if "/contents/" in c)


def test_the_first_refresh_fetches_the_overview_the_imperson_folder_and_the_files_policies_name(tmp_path):
    github = FakeGitHub(FILES)
    changed = tenant.refresh(REPO, "tok", tmp_path, opener=github)
    assert sorted(changed) == fetched(github) == [
        ".imperson/skills/spend/SKILL.md", ".imperson/spend/policy.yaml", ".imperson/spend/rubric.md",
        ".team/people.yaml", "business-overview.md"]
    assert (tmp_path / ".imperson/spend/policy.yaml").read_bytes() == POLICY
    assert not (tmp_path / "README.md").exists() and not (tmp_path / ".team/cases").exists()
    state = json.loads((tmp_path / "tenant.json").read_text())
    assert state["branch"] == "main" and state["commit"] == "c1" and set(state["blobs"]) == set(changed)


def test_an_unchanged_branch_costs_one_conditional_request(tmp_path):
    github = FakeGitHub(FILES)
    tenant.refresh(REPO, "tok", tmp_path, opener=github)
    github.calls.clear()
    assert tenant.refresh(REPO, "tok", tmp_path, opener=github) == []
    assert github.calls == [f"{tenant.API}/repos/{REPO}/branches/main"]


def test_only_the_changed_file_is_fetched_and_a_removed_one_is_deleted(tmp_path):
    github = FakeGitHub(FILES)
    tenant.refresh(REPO, "tok", tmp_path, opener=github)
    github.files[".imperson/spend/rubric.md"] = b"# Rubric, revised\n"
    del github.files[".imperson/skills/spend/SKILL.md"]
    github.commit = "c2"
    github.calls.clear()
    changed = tenant.refresh(REPO, "tok", tmp_path, opener=github)
    assert sorted(changed) == [".imperson/skills/spend/SKILL.md", ".imperson/spend/rubric.md"]
    assert fetched(github) == [".imperson/spend/rubric.md"]
    assert (tmp_path / ".imperson/spend/rubric.md").read_bytes() == b"# Rubric, revised\n"
    assert not (tmp_path / ".imperson/skills/spend/SKILL.md").exists()


def test_a_commit_elsewhere_in_the_repository_fetches_nothing(tmp_path):
    github = FakeGitHub(FILES)
    tenant.refresh(REPO, "tok", tmp_path, opener=github)
    github.files["README.md"] = b"changed\n"
    github.commit = "c2"
    github.calls.clear()
    assert tenant.refresh(REPO, "tok", tmp_path, opener=github) == []
    assert fetched(github) == []
    assert json.loads((tmp_path / "tenant.json").read_text())["commit"] == "c2"


def test_a_named_branch_is_read_and_the_default_otherwise(tmp_path):
    github = FakeGitHub(FILES, branch="live")
    tenant.refresh(REPO, "tok", tmp_path / "named", branch="live", opener=github)
    assert f"{tenant.API}/repos/{REPO}" not in github.calls
    github.calls.clear()
    tenant.refresh(REPO, "tok", tmp_path / "default", opener=github)
    assert github.calls[0] == f"{tenant.API}/repos/{REPO}"


def test_a_refused_token_names_itself(tmp_path):
    with pytest.raises(RuntimeError, match="token"):
        tenant.refresh(REPO, "wrong", tmp_path, opener=FakeGitHub(FILES))

"""The business's files, read from its GitHub repository through the API and kept in a cache.

imperson assumes no clone. It works from `business-overview.md`, the `.imperson/` tree and each file a policy
names under `team`, kept in the cache at their repository paths. A refresh asks for the branch head with the last
ETag, and only when the head has moved compares each wanted path's blob sha with the cached one and fetches those
that differ; a path gone from the tree is removed. `tenant.json` beside the files holds the branch, the commit,
the ETag and the blob sha of each path. The token needs to read the repository's contents and nothing else.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

import yaml

API = "https://api.github.com"
STATE = "tenant.json"
OVERVIEW = "business-overview.md"
FOLDER = ".imperson"


def _request(opener, url: str, token: str, *, etag: str | None = None, raw: bool = False) -> tuple[int, str | None, bytes]:
    """One API call: the status, the ETag and the body. A 304 is a status, not an error."""
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}", "User-Agent": "imperson", "X-GitHub-Api-Version": "2022-11-28",
        "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json"})
    if etag:
        req.add_header("If-None-Match", etag)
    try:
        with opener(req) as resp:
            return resp.status, resp.headers.get("ETag"), resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return 304, etag, b""
        if exc.code == 401:
            raise RuntimeError(f"GitHub refused the token for {url}: it has expired or cannot read this "
                               "repository's contents; issue a new one") from None
        raise RuntimeError(f"GitHub answered {exc.code} for {url}") from None


def refresh(repo: str, token: str, cache: Path, *, branch: str | None = None,
            opener=urllib.request.urlopen) -> list[str]:
    """Bring the cache up to the branch head; the repository paths written or removed."""
    cache.mkdir(parents=True, exist_ok=True)
    state_file = cache / STATE
    state = json.loads(state_file.read_text()) if state_file.exists() else {"blobs": {}}

    def get(path: str, **kw):
        return _request(opener, f"{API}/repos/{repo}" + (f"/{path}" if path else ""), token, **kw)

    branch = branch or state.get("branch") or json.loads(get("")[2])["default_branch"]
    status, etag, body = get(f"branches/{branch}", etag=state.get("etag") if branch == state.get("branch") else None)
    if status == 304:
        return []
    head = json.loads(body)
    commit = head["commit"]["sha"]
    state.update(branch=branch, etag=etag)
    if commit == state.get("commit"):
        state_file.write_text(json.dumps(state, indent=1))
        return []

    trees: dict[str, list[dict]] = {}

    def tree(sha: str, recursive: bool = False) -> list[dict]:
        if sha not in trees:
            found = json.loads(get(f"git/trees/{sha}" + ("?recursive=1" if recursive else ""))[2])
            if found.get("truncated"):
                raise RuntimeError(f"the tree {sha} of {repo} is too large for one listing")
            trees[sha] = found["tree"]
        return trees[sha]

    def entry(path: str) -> dict | None:
        found: dict | None = {"type": "tree", "sha": head["commit"]["commit"]["tree"]["sha"]}
        for part in path.split("/"):
            if found is None or found["type"] != "tree":
                return None
            found = next((e for e in tree(found["sha"]) if e["path"] == part), None)
        return found

    wanted: dict[str, str] = {}
    changed: list[str] = []

    def want(path: str) -> None:
        found = entry(path)
        if found is None:
            return
        if found["type"] == "blob":
            wanted[path] = found["sha"]
        elif found["type"] == "tree":
            wanted.update({f"{path}/{e['path']}": e["sha"] for e in tree(found["sha"], recursive=True)
                           if e["type"] == "blob"})
        for p, sha in wanted.items():
            if p not in changed and (state["blobs"].get(p) != sha or not (cache / p).exists()):
                (cache / p).parent.mkdir(parents=True, exist_ok=True)
                (cache / p).write_bytes(get(f"contents/{quote(p)}?ref={commit}", raw=True)[2])
                changed.append(p)

    want(OVERVIEW)
    want(FOLDER)
    for policy in sorted((cache / FOLDER).glob("*/policy.yaml")):
        team = (yaml.safe_load(policy.read_text()) or {}).get("team")
        if team:
            want(team)
    for path in sorted(set(state["blobs"]) - set(wanted)):
        (cache / path).unlink(missing_ok=True)
        changed.append(path)
    state.update(commit=commit, blobs=wanted)
    state_file.write_text(json.dumps(state, indent=1))
    return changed

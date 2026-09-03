"""Keep local shallow clones of the source repos in sync.

`git fetch` is the update mechanism, not a scraper of the rendered site: it yields
the same .mdx files ripgrep already searches, keeps line numbers meaningful, and
reports exactly what changed. Clones are shallow (depth 1) and track the default
branch, so the agent never cites an unmerged feature branch.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from pathlib import Path

from . import llms
from .config import settings

log = logging.getLogger(__name__)

_lock = threading.Lock()
_heads: dict[str, str] = {}


def _run(args: list[str], cwd: Path | None = None, timeout: int = 300) -> tuple[int, str]:
    proc = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=timeout
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _auth_url(url: str) -> str:
    """Inject a token for private repos. Never logged."""
    token = settings.github_token
    if token and url.startswith("https://github.com/"):
        return url.replace("https://github.com/", f"https://x-access-token:{token}@github.com/")
    return url


def head_sha(path: Path) -> str | None:
    code, out = _run(["git", "rev-parse", "--short", "HEAD"], cwd=path)
    return out if code == 0 else None


def sync_one(name: str, url: str, branch: str, dest: Path, sparse: list[str]) -> str | None:
    """Clone or fast-forward one repo. Returns the resulting short SHA."""
    try:
        if not (dest / ".git").is_dir():
            dest.parent.mkdir(parents=True, exist_ok=True)
            args = ["git", "clone", "--depth", "1", "--branch", branch]
            if sparse:
                args += ["--filter=blob:none", "--sparse"]
            code, out = _run(args + [_auth_url(url), str(dest)])
            if code != 0:
                log.error("clone failed for %s: %s", name, out.replace(settings.github_token or "\0", "***"))
                return None
            if sparse:
                _run(["git", "sparse-checkout", "set", *sparse], cwd=dest)
        else:
            code, out = _run(["git", "fetch", "--depth", "1", "origin", branch], cwd=dest)
            if code != 0:
                log.error("fetch failed for %s: %s", name, out)
                return head_sha(dest)
            _run(["git", "reset", "--hard", f"origin/{branch}"], cwd=dest)
        return head_sha(dest)
    except subprocess.TimeoutExpired:
        log.error("git timed out for %s", name)
        return head_sha(dest) if dest.is_dir() else None


def sync_all() -> dict[str, str]:
    """Sync every configured repo. Returns {name: short sha}."""
    with _lock:
        for repo in settings.repo_list():
            before = _heads.get(repo["name"])
            sha = sync_one(
                repo["name"],
                repo["url"],
                repo.get("branch", "main"),
                Path(settings.repo_dir) / repo["name"],
                repo.get("sparse", []),
            )
            if sha:
                _heads[repo["name"]] = sha
                if before and before != sha:
                    log.info("%s updated %s -> %s", repo["name"], before, sha)
                elif not before:
                    log.info("%s at %s", repo["name"], sha)
        return dict(_heads)


def heads() -> dict[str, str]:
    return dict(_heads)


def start_background_sync() -> threading.Thread:
    def loop() -> None:
        while True:
            time.sleep(settings.repo_sync_minutes * 60)
            try:
                sync_all()
            except Exception:
                log.exception("scheduled repo sync failed")
            try:
                llms.sync_all()
            except Exception:
                log.exception("scheduled llms.txt sync failed")

    t = threading.Thread(target=loop, name="repo-sync", daemon=True)
    t.start()
    return t

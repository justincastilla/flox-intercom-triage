"""Fetch flox.dev's llms.txt corpora into the search tree.

Replaces cloning the (private, 1.4GB) website repo. The published llms-full.txt
carries every article with an explicit `Source: <url>` marker per document, so a
ripgrep hit maps to a canonical published URL — better provenance than a file path,
and it can only reference content that is actually live.

Only llms-full.txt is fetched by default: /docs/llms-full.txt duplicates the
flox/docs clone (which has line numbers), and the llms.txt indexes are link lists.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx2 as httpx

from .config import settings

log = logging.getLogger(__name__)

SOURCE_MARKER = "Source: "


def target_dir() -> Path:
    return Path(settings.repo_dir) / "llms"


def sync_all() -> dict[str, int]:
    """Download each configured corpus. Returns {filename: bytes}."""
    out: dict[str, int] = {}
    dest = target_dir()
    dest.mkdir(parents=True, exist_ok=True)
    for url in settings.llms_url_list():
        name = url.rstrip("/").rsplit("/", 1)[-1] or "llms.txt"
        try:
            with httpx.Client(timeout=60.0, follow_redirects=True) as client:
                resp = client.get(url)
                resp.raise_for_status()
                body = resp.text
        except Exception:
            log.exception("could not fetch %s", url)
            continue
        path = dest / name
        previous = path.read_text() if path.is_file() else None
        if previous != body:
            path.write_text(body)
            log.info("%s updated (%d bytes)", name, len(body))
        out[name] = len(body)
    return out


def source_for_line(path: Path, line: int) -> str | None:
    """The canonical URL of the document containing this line.

    Documents are delimited by `Source: <url>` headers, so scan back to the nearest
    one rather than guessing a URL from the filename.
    """
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return None
    for idx in range(min(line, len(lines)) - 1, -1, -1):
        text = lines[idx]
        if text.startswith(SOURCE_MARKER):
            return text[len(SOURCE_MARKER):].strip() or None
    return None

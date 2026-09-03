"""Turn a source reference into a public URL.

The model never writes URLs — it reports what its tools returned (a local file path
with a line number, or an Intercom conversation id) and the mapping happens here. A
fabricated link is the worst kind of error in a note: it looks authoritative and
costs a click to disprove.

Mapping keys on which repo a path belongs to, not on path shape, because the local
checkout layout (~/Code/flox/flox) and the container layout (/app/repos/flox) differ.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from . import llms
from .config import settings

# Verified against the live workspace: this path opens the conversation directly.
CONVERSATION_URL = "https://app.intercom.com/a/inbox/{app_id}/inbox/shared/all/conversation/{cid}"

_REF = re.compile(r"^(?P<path>[^\s:]+?):(?P<line>\d+)(?:-\d+)?$")


def split_ref(ref: str) -> tuple[str, str | None]:
    m = _REF.match(ref.strip())
    return (m.group("path"), m.group("line")) if m else (ref.strip(), None)


def conversation_url(cid: str, app_id: str | None) -> str | None:
    if not app_id or not cid.isdigit():
        return None
    return CONVERSATION_URL.format(app_id=app_id, cid=cid)


def _repo_of(path: str) -> tuple[str, PurePosixPath] | None:
    """Which configured root contains this path, and where inside it."""
    # resolve() on both sides: docs.py hands ripgrep resolved roots, so hits come
    # back with symlinks expanded (/tmp -> /private/tmp on macOS). Comparing an
    # unresolved root against a resolved hit silently drops every link.
    resolved = Path(path).expanduser().resolve()
    for root in settings.doc_root_list():
        root_path = Path(root).expanduser().resolve()
        try:
            rel = resolved.relative_to(root_path)
        except ValueError:
            continue
        return root_path.name, PurePosixPath(str(rel))
    return None


def _docs_url(rel: PurePosixPath, line: str, sha: str | None) -> str | None:
    return f"https://flox.dev/docs/{rel.with_suffix('')}/"


def _flox_url(rel: PurePosixPath, line: str, sha: str | None) -> str | None:
    # Permalink at the commit the agent actually read, so a later docs change
    # cannot silently rewrite what a stored brief appears to cite.
    ref = sha or "main"
    return f"https://github.com/flox/flox/blob/{ref}/{rel}#L{line}"


def _website_url(rel: PurePosixPath, line: str, sha: str | None) -> str | None:
    parts = rel.parts
    if len(parts) >= 3 and parts[0] == "src" and parts[1] in ("posts", "news"):
        return f"https://flox.dev/blog/{rel.stem}/"
    return None


REPO_URL_BUILDERS = {
    "docs": _docs_url,
    "flox": _flox_url,
    "floxwebsite": _website_url,
}


def source_url(ref: str, app_id: str | None = None, heads: dict[str, str] | None = None) -> str | None:
    ref = ref.strip()
    if ref.isdigit():
        return conversation_url(ref, app_id)

    path, line = split_ref(ref)
    found = _repo_of(path)
    if not found:
        return None
    repo, rel = found
    if repo == "llms":
        # Each document in an llms.txt corpus is headed by its canonical URL.
        return llms.source_for_line(Path(path).expanduser().resolve(), int(line or 1))
    builder = REPO_URL_BUILDERS.get(repo)
    if not builder or not rel.name:
        return None
    return builder(rel, line or "1", (heads or {}).get(repo))

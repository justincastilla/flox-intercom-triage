"""Local docs search via ripgrep, confined to configured roots."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .config import settings

MAX_MATCHES = 25
MAX_READ_LINES = 200


def _roots() -> list[Path]:
    return [Path(p).expanduser().resolve() for p in settings.doc_root_list()]


def _within_roots(path: Path) -> bool:
    resolved = path.resolve()
    return any(resolved == r or r in resolved.parents for r in _roots())


def search(pattern: str, max_results: int = MAX_MATCHES) -> list[dict]:
    roots = _roots()
    if not roots:
        return []
    cmd = [
        "rg",
        "--no-heading",
        "--line-number",
        "--ignore-case",
        "--max-columns", "400",
        "--max-count", "5",
        "--type-add", "docs:*.{md,mdx,txt,rst,toml,nix,yaml,yml}",
        "--type", "docs",
        "-e", pattern,
        *[str(r) for r in roots],
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    hits: list[dict] = []
    for line in proc.stdout.splitlines()[:max_results]:
        path, _, rest = line.partition(":")
        lineno, _, text = rest.partition(":")
        if not lineno.isdigit():
            continue
        hits.append({"path": path, "line": int(lineno), "text": text.strip()[:400]})
    return hits


def read(path: str, start_line: int = 1, num_lines: int = 60) -> str:
    target = Path(path).expanduser()
    if not _within_roots(target):
        return f"refused: {path} is outside the configured doc roots"
    if not target.is_file():
        return f"not found: {path}"
    num_lines = max(1, min(num_lines, MAX_READ_LINES))
    start = max(1, start_line)
    lines = target.read_text(errors="replace").splitlines()[start - 1 : start - 1 + num_lines]
    rel = os.path.relpath(target, target.parent.parent)
    return f"# {rel} (lines {start}-{start + len(lines) - 1})\n" + "\n".join(lines)

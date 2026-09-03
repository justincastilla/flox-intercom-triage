"""Aggregate documentation gaps surfaced while answering real questions.

Each brief records what the agent looked for and could not find. Grouped by where
the answer would belong, recurrence becomes visible: a gap hit once is a curiosity,
the same gap hit five times is a page someone should write.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from pathlib import Path

from . import store
from .config import settings


def _group(gaps: list[dict]) -> list[tuple[str, list[dict]]]:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for gap in gaps:
        key = (gap.get("suggested_location") or "").strip() or "(location unclear)"
        buckets[key].append(gap)
    # Most-hit locations first; recurrence is the signal worth acting on.
    return sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0]))


def render_markdown(limit: int = 500) -> str:
    gaps = store.fetch_gaps(limit)
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Documentation gaps",
        "",
        f"_Generated {now} from {len(gaps)} gap(s) recorded across triaged tickets._",
        "",
        "Each entry is something the triage agent searched for while answering a real "
        "customer question and could not find. Recurrence is the signal: repeated "
        "entries under one location are the pages worth writing first.",
        "",
    ]
    if not gaps:
        lines += ["No gaps recorded yet.", ""]
        return "\n".join(lines)

    for location, items in _group(gaps):
        lines.append(f"## {location} — {len(items)} ticket(s)")
        lines.append("")
        for gap in items:
            lines.append(f"- **{gap['question'].strip()}**")
            if gap.get("missing"):
                lines.append(f"  - Missing: {gap['missing'].strip()}")
            if gap.get("nearest_existing"):
                lines.append(f"  - Nearest today: {gap['nearest_existing'].strip()}")
            cid = gap.get("conversation_id")
            if cid:
                lines.append(f"  - Ticket: `{cid}`")
        lines.append("")
    return "\n".join(lines)


def write_report(path: str | None = None) -> Path:
    target = Path(path or settings.gap_report_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_markdown())
    return target

"""Render a Brief into the HTML subset Intercom accepts in a note body.

Section order is deliberate: a teammate opening the ticket should be able to stop
reading at any rule and still have the most useful thing they've seen so far.
Summary first, then what it's based on, then how much to trust it, then who should
take it, and the draft last — it's the longest block and only matters once they've
decided to act.
"""

from __future__ import annotations

import html

from .config import settings
from .links import source_url
from .triage import Brief

HEADER = "🤖 Knowledge Gnome is here to help! (AI-generated · internal only)"

# Conversation notes keep only b, i, br, p, ul, li — <hr>, headings and blockquote
# are silently stripped (the published allowed-HTML list covers Articles, not
# notes). The section rule is therefore drawn with text.
RULE = "<p>" + "─" * 30 + "</p>"


def _esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def _para(text: str) -> str:
    return f"<p>{_esc(text).replace(chr(10), '<br>')}</p>"


def _label(ref: str) -> str:
    """Readable display text — local checkout paths trimmed to something portable."""
    ref = ref.strip()
    if ref.isdigit():
        return f"Past ticket {ref}"
    for root in settings.doc_root_list():
        parent = root.rstrip("/").rsplit("/", 1)[0]
        if ref.startswith(parent + "/"):
            return ref[len(parent) + 1 :]
        if ref.startswith(root + "/"):
            return ref[len(root) + 1 :]
    return ref


def _source_item(
    ref: str, why: str, app_id: str | None, heads: dict[str, str] | None = None
) -> str:
    label = _esc(_label(ref))
    url = source_url(ref, app_id, heads)
    anchor = f'<a href="{html.escape(url, quote=True)}">{label}</a>' if url else f"<b>{label}</b>"
    return f"<li>{anchor} — {_esc(why)}</li>"


def note_html(
    brief: Brief, app_id: str | None = None, heads: dict[str, str] | None = None
) -> str:
    out = [f"<p><b>{_esc(HEADER)}</b></p>", RULE]

    # 1 — what this is, for whoever just opened the ticket
    out.append(_para(brief.summary))
    if brief.customer_context:
        out.append(_para(brief.customer_context))

    # 2 — what it's based on
    out.append(RULE)
    if brief.sources:
        items = "".join(_source_item(s.ref, s.why, app_id, heads) for s in brief.sources)
        out.append(f"<p><b>Sources</b></p><ul>{items}</ul>")
    else:
        out.append("<p><b>Sources:</b> none found.</p>")

    # 3 — how much to trust it
    out.append(RULE)
    out.append(f"<p><b>Confidence:</b> {brief.confidence:.0%}</p>")
    if brief.handling_notes:
        out.append(f"<p><b>⚠️ Before you reply:</b> {_esc(brief.handling_notes)}</p>")

    # 4 — who should take it
    out.append(RULE)
    out.append(f"<p><b>Route to:</b> {_esc(brief.suggested_team) or 'unclear'}</p>")

    # 5 — the draft, last
    out.append(RULE)
    if brief.draft_reply:
        out.append("<p><b>Draft reply</b> — review before sending</p>")
        out.append(_para(brief.draft_reply))
    else:
        out.append("<p><b>Draft reply:</b> none — not enough to go on.</p>")

    return "".join(out)

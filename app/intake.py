"""Learn which opening lines are Messenger workflow button labels.

A Messenger flow creates the conversation with a canned category line, a bot asks
for detail, and the customer's real question arrives as a reply minutes later.
Triaging on `conversation.user.created` alone would run against the category line.

Button labels are identifiable without hardcoding copy: they appear verbatim across
many conversations, whereas a typed question is effectively never repeated. Learned
from the closed-ticket corpus and cached on disk.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from pathlib import Path

from .config import settings
from .intercom import Intercom, strip_html

log = logging.getLogger(__name__)

CACHE = Path(settings.intake_cache_path)
MIN_REPEATS = 2
SAMPLE_SIZE = 150


def _normalise(text: str) -> str:
    return " ".join(text.split()).strip().lower()


def learn(intercom: Intercom) -> set[str]:
    """Opening lines seen in >= MIN_REPEATS closed conversations, excluding
    anything ending in '?' (a repeated question is still a question)."""
    body = {
        "query": {"field": "state", "operator": "=", "value": "closed"},
        "pagination": {"per_page": min(SAMPLE_SIZE, 150)},
    }
    convs = intercom._json(
        intercom._http.post("/conversations/search", json=body)
    ).get("conversations", [])
    counts = Counter(
        _normalise(strip_html((c.get("source") or {}).get("body")))
        for c in convs
    )
    return {
        text
        for text, n in counts.items()
        if n >= MIN_REPEATS and text and not text.endswith("?")
    }


def load(intercom: Intercom | None = None, max_age_days: int = 7) -> set[str]:
    if CACHE.is_file():
        age = time.time() - CACHE.stat().st_mtime
        if age < max_age_days * 86400:
            return set(json.loads(CACHE.read_text()))
    if intercom is None:
        return set()
    try:
        labels = learn(intercom)
    except Exception:
        log.exception("could not learn intake labels; treating all openings as real")
        return set()
    CACHE.write_text(json.dumps(sorted(labels), indent=1))
    log.info("learned %d Messenger intake labels", len(labels))
    return labels

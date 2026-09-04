"""Spend and concurrency limits for triage runs.

The Messenger is public, so anyone can open a conversation and send messages, and
every customer message is eligible for a fresh brief. One brief is ~14 sequential
Opus calls with accumulating context, so an unthrottled burst is both a real bill
and a way to exhaust the API rate limit for everything else.

Four separate limits, because they fail differently:
  - concurrency: a burst serializes instead of fanning out across the threadpool
  - cooldown:    rapid-fire messages in one conversation collapse into one brief
  - per-conversation cap: one chatty thread cannot dominate the budget
  - hourly cap:  a circuit breaker across everything, the backstop for the rest

State is in-memory, matching the existing dedupe: single worker only.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

from .config import settings

log = logging.getLogger(__name__)

_lock = threading.Lock()
_semaphore = threading.BoundedSemaphore(settings.max_concurrent_triage)
_hourly: deque[float] = deque()
_per_conversation: dict[str, deque[float]] = {}


def _prune(now: float) -> None:
    while _hourly and now - _hourly[0] > 3600:
        _hourly.popleft()
    for cid, stamps in list(_per_conversation.items()):
        while stamps and now - stamps[0] > 86400:
            stamps.popleft()
        if not stamps:
            del _per_conversation[cid]


def acquire(conversation_id: str, force: bool = False) -> str | None:
    """Reserve a triage slot. Returns None when allowed, else a refusal reason.

    Blocks while at capacity rather than refusing outright: the webhook has
    already been acknowledged, so dropping the work loses the brief entirely.
    """
    with _lock:
        now = time.monotonic()
        _prune(now)
        if len(_hourly) >= settings.max_triage_runs_per_hour:
            return "hourly_cap"
        stamps = _per_conversation.get(conversation_id)
        # A teammate asking explicitly bypasses the cooldown and per-conversation
        # cap, but never the concurrency or hourly limits — those exist to stop a
        # spend runaway, which an authenticated teammate can cause just as easily.
        if stamps and not force:
            if now - stamps[-1] < settings.conversation_cooldown_seconds:
                return "cooldown"
            if len(stamps) >= settings.max_briefs_per_conversation:
                return "conversation_cap"

    if not _semaphore.acquire(timeout=settings.triage_queue_timeout_seconds):
        return "queue_timeout"

    with _lock:
        now = time.monotonic()
        _hourly.append(now)
        _per_conversation.setdefault(conversation_id, deque()).append(now)
    return None


def release() -> None:
    try:
        _semaphore.release()
    except ValueError:  # released more than acquired; never fatal
        log.warning("limits.release() called without a matching acquire")


def snapshot() -> dict:
    with _lock:
        _prune(time.monotonic())
        return {
            "runs_last_hour": len(_hourly),
            "hourly_cap": settings.max_triage_runs_per_hour,
            "conversations_tracked": len(_per_conversation),
            "max_concurrent": settings.max_concurrent_triage,
        }

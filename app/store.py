"""Durable storage for briefs and the doc gaps they surface.

Postgres when DATABASE_URL is set (the cloud), JSONL otherwise (local dev), so the
same code path works in both. Gaps live in their own table rather than inside the
brief JSON: the point of collecting them is to aggregate across tickets, and that
is a GROUP BY, not a scan of nested documents.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .config import settings

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS briefs (
    id                BIGSERIAL PRIMARY KEY,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    conversation_id   TEXT NOT NULL,
    posted            BOOLEAN NOT NULL,
    skipped_reason    TEXT,
    confidence        REAL,
    worth_posting     BOOLEAN,
    suggested_team    TEXT,
    summary           TEXT,
    customer_context  TEXT,
    draft_reply       TEXT,
    handling_notes    TEXT,
    sources           JSONB,
    repo_heads        JSONB,
    model             TEXT,
    duration_ms       INTEGER,
    fingerprint       TEXT,
    input_tokens      INTEGER,
    output_tokens     INTEGER
);
-- Added after the table shipped; CREATE TABLE IF NOT EXISTS will not add them.
ALTER TABLE briefs ADD COLUMN IF NOT EXISTS input_tokens INTEGER;
ALTER TABLE briefs ADD COLUMN IF NOT EXISTS output_tokens INTEGER;
CREATE INDEX IF NOT EXISTS briefs_conversation_idx ON briefs (conversation_id);
CREATE INDEX IF NOT EXISTS briefs_created_idx ON briefs (created_at DESC);

CREATE TABLE IF NOT EXISTS doc_gaps (
    id                 BIGSERIAL PRIMARY KEY,
    brief_id           BIGINT REFERENCES briefs(id) ON DELETE CASCADE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    conversation_id    TEXT NOT NULL,
    question           TEXT NOT NULL,
    missing            TEXT,
    suggested_location TEXT,
    nearest_existing   TEXT,
    resolved           BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS doc_gaps_location_idx ON doc_gaps (suggested_location);
CREATE INDEX IF NOT EXISTS doc_gaps_created_idx ON doc_gaps (created_at DESC);
"""

_pool = None


def _connect():
    global _pool
    if _pool is None:
        import psycopg_pool

        _pool = psycopg_pool.ConnectionPool(settings.database_url, min_size=1, max_size=4)
    return _pool


def enabled() -> bool:
    return bool(settings.database_url)


def init() -> None:
    if not enabled():
        log.info("DATABASE_URL unset — briefs go to %s", settings.brief_log_path)
        return
    with _connect().connection() as conn:
        conn.execute(SCHEMA)
    log.info("brief storage ready (postgres)")


def record(
    conversation_id: str,
    brief: Any | None,
    posted: bool,
    *,
    skipped_reason: str | None = None,
    repo_heads: dict[str, str] | None = None,
    duration_ms: int | None = None,
    fingerprint: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> int | None:
    """Persist one triage outcome. Returns the brief row id, when there is one."""
    payload = json.loads(brief.model_dump_json()) if brief is not None else None

    if not enabled():
        with open(settings.brief_log_path, "a") as fh:
            fh.write(json.dumps({
                "conversation_id": conversation_id, "posted": posted,
                "skipped_reason": skipped_reason, "repo_heads": repo_heads,
                "duration_ms": duration_ms, "input_tokens": input_tokens,
                "output_tokens": output_tokens, "brief": payload,
            }) + "\n")
        return None

    try:
        with _connect().connection() as conn:
            row = conn.execute(
                """INSERT INTO briefs (conversation_id, posted, skipped_reason, confidence,
                       worth_posting, suggested_team, summary, customer_context, draft_reply,
                       handling_notes, sources, repo_heads, model, duration_ms, fingerprint,
                       input_tokens, output_tokens)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (
                    conversation_id, posted, skipped_reason,
                    payload and payload.get("confidence"),
                    payload and payload.get("worth_posting"),
                    payload and payload.get("suggested_team"),
                    payload and payload.get("summary"),
                    payload and payload.get("customer_context"),
                    payload and payload.get("draft_reply"),
                    payload and payload.get("handling_notes"),
                    json.dumps(payload.get("sources")) if payload else None,
                    json.dumps(repo_heads or {}),
                    settings.triage_model, duration_ms, fingerprint,
                    input_tokens, output_tokens,
                ),
            ).fetchone()
            brief_id = row[0]
            for gap in (payload or {}).get("doc_gaps") or []:
                conn.execute(
                    """INSERT INTO doc_gaps (brief_id, conversation_id, question, missing,
                           suggested_location, nearest_existing)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (brief_id, conversation_id, gap["question"], gap["missing"],
                     gap.get("suggested_location"), gap.get("nearest_existing")),
                )
            return brief_id
    except Exception:
        log.exception("could not persist brief for %s", conversation_id)
        return None


def fetch_gaps(limit: int = 500) -> list[dict]:
    if not enabled():
        out = []
        try:
            for line in open(settings.brief_log_path):
                rec = json.loads(line)
                for gap in (rec.get("brief") or {}).get("doc_gaps") or []:
                    out.append({**gap, "conversation_id": rec["conversation_id"], "created_at": None})
        except FileNotFoundError:
            pass
        return out[-limit:]
    with _connect().connection() as conn:
        rows = conn.execute(
            """SELECT question, missing, suggested_location, nearest_existing,
                      conversation_id, created_at
               FROM doc_gaps WHERE NOT resolved ORDER BY created_at DESC LIMIT %s""",
            (limit,),
        ).fetchall()
    keys = ["question", "missing", "suggested_location", "nearest_existing",
            "conversation_id", "created_at"]
    return [dict(zip(keys, r)) for r in rows]

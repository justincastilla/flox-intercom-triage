"""FastAPI service: receive Intercom webhooks, triage in the background, post a note."""

from __future__ import annotations

import logging
import secrets
import threading
import time
from contextlib import asynccontextmanager

import anthropic
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse

from .config import settings
from . import gaps, intake, llms, repos, store
from .intercom import (
    Intercom,
    awaiting_customer_detail,
    customer_fingerprint,
    verify_signature,
)
from .render import note_html
from .triage import Brief, triage

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("triage")

TRIGGER_TOPICS = {
    "conversation.user.created",
    # Messenger flows open with a category line and the real question arrives
    # as a reply minutes later, so creation alone is not enough.
    "conversation.user.replied",
    "ticket.created",
}

_state: dict = {}
_briefed: dict[str, str] = {}
_briefed_lock = threading.Lock()


def _claim(conversation_id: str, fingerprint: str) -> bool:
    """Claim a conversation for triage, once. Single-process — replace with
    Redis/Postgres before running more than one worker.

    Claimed at the point of triage, not on webhook receipt, so a conversation
    skipped while awaiting customer detail can still be picked up by the
    `conversation.user.replied` event that follows."""
    with _briefed_lock:
        if _briefed.get(conversation_id) == fingerprint:
            return False
        _briefed[conversation_id] = fingerprint
        return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.intercom_client_secret and not settings.allow_unsigned_webhooks:
        log.warning(
            "INTERCOM_CLIENT_SECRET is unset — webhooks will be rejected. "
            "Set ALLOW_UNSIGNED_WEBHOOKS=true only for local testing."
        )
    store.init()
    repos.sync_all()
    llms.sync_all()
    repos.start_background_sync()
    _state["intercom"] = Intercom()
    _state["anthropic"] = anthropic.Anthropic()
    _state["admin_id"] = None
    _state["intake_labels"] = intake.load(_state["intercom"])
    try:
        _state["app_id"] = (_state["intercom"]._json(
            _state["intercom"]._http.get("/me")).get("app") or {}).get("id_code")
    except Exception:
        log.exception("could not resolve the workspace id; source links will be plain text")
        _state["app_id"] = None
    if settings.post_notes:
        try:
            _state["admin_id"] = _state["intercom"].operator_admin_id()
        except Exception:
            log.exception("could not resolve the Operator admin id")
    log.info(
        "started — post_notes=%s min_confidence=%.2f intake_labels=%d doc_roots=%s",
        settings.post_notes,
        settings.min_confidence,
        len(_state["intake_labels"]),
        settings.doc_root_list() or "(none)",
    )
    log.info("source repos: %s", repos.heads() or "(none)")
    yield
    _state["intercom"].close()


app = FastAPI(title="Intercom pre-triage", lifespan=lifespan)


def run_triage(conversation_id: str) -> None:
    intercom: Intercom = _state["intercom"]
    brief: Brief | None = None
    posted = False
    skipped: str | None = None
    fingerprint: str | None = None
    started = time.monotonic()
    try:
        conversation = intercom.get_conversation(conversation_id)
        if awaiting_customer_detail(conversation, _state["intake_labels"]):
            log.info(
                "%s: opening is a Messenger intake selection, waiting for the "
                "customer's question", conversation_id)
            return
        fingerprint = customer_fingerprint(conversation)
        if not _claim(conversation_id, fingerprint):
            log.info("%s: already briefed for this customer content", conversation_id)
            return
        brief = triage(conversation, intercom, _state["anthropic"])
        if brief is None:
            log.info("%s: no brief produced", conversation_id)
            skipped = "no_brief"
        elif brief.confidence < settings.min_confidence:
            log.info(
                "%s: confidence %.2f below threshold, staying quiet",
                conversation_id,
                brief.confidence,
            )
            skipped = "low_confidence"
        elif settings.require_worth_posting and not brief.worth_posting:
            log.info("%s: agent judged a note not worth posting", conversation_id)
            skipped = "not_worth_posting"
        elif not settings.post_notes:
            log.info("%s: shadow mode, brief logged but not posted", conversation_id)
            skipped = "shadow_mode"
        elif not _state.get("admin_id"):
            log.error("%s: no Operator admin id, cannot post", conversation_id)
            skipped = "no_admin_id"
        else:
            intercom.post_note(
                conversation_id,
                _state["admin_id"],
                note_html(brief, _state.get("app_id"), repos.heads()),
            )
            posted = True
            log.info("%s: note posted", conversation_id)
    except Exception:
        log.exception("triage failed for %s", conversation_id)
        skipped = skipped or "error"
    finally:
        store.record(
            conversation_id, brief, posted,
            skipped_reason=skipped,
            repo_heads=repos.heads(),
            duration_ms=int((time.monotonic() - started) * 1000),
            fingerprint=fingerprint,
        )
        if brief is not None and brief.doc_gaps:
            try:
                gaps.write_report()
            except Exception:
                log.exception("could not refresh the doc-gap report")


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "post_notes": settings.post_notes}


@app.get("/reports/doc-gaps.md", response_class=PlainTextResponse)
def doc_gap_report(x_triage_token: str | None = Header(default=None)) -> str:
    """The doc-gap report as Markdown. Token-gated: it paraphrases customer questions."""
    if not settings.triage_admin_token:
        raise HTTPException(status_code=404, detail="not found")
    if not x_triage_token or not secrets.compare_digest(
        x_triage_token, settings.triage_admin_token
    ):
        raise HTTPException(status_code=401, detail="bad or missing X-Triage-Token")
    return gaps.render_markdown()


@app.post("/webhooks/intercom")
async def webhook(
    request: Request,
    background: BackgroundTasks,
    x_hub_signature: str | None = Header(default=None),
) -> dict:
    raw = await request.body()
    if not verify_signature(raw, x_hub_signature):
        raise HTTPException(status_code=401, detail="bad signature")

    payload = await request.json()
    topic = payload.get("topic")
    item = (payload.get("data") or {}).get("item") or {}
    conversation_id = str(item.get("id") or "")
    log.info(
        "webhook received: topic=%s conversation=%s", topic, conversation_id or "-"
    )

    if topic == "ping":
        return {"ok": True, "pong": True}
    if topic not in TRIGGER_TOPICS:
        return {"ok": True, "skipped": topic}
    if not conversation_id:
        return {"ok": True, "skipped": "no conversation id"}

    background.add_task(run_triage, conversation_id)
    return {"ok": True, "queued": conversation_id}


@app.post("/triage/{conversation_id}")
def triage_now(
    conversation_id: str, x_triage_token: str | None = Header(default=None)
) -> dict:
    """Manual trigger — run the agent against one conversation and return the brief.

    Used for shadow-mode evaluation. Never posts a note. Requires TRIAGE_ADMIN_TOKEN
    because it spends model budget and returns customer ticket content; with no token
    configured the endpoint is disabled rather than open.
    """
    if not settings.triage_admin_token:
        raise HTTPException(status_code=404, detail="not found")
    if not x_triage_token or not secrets.compare_digest(
        x_triage_token, settings.triage_admin_token
    ):
        raise HTTPException(status_code=401, detail="bad or missing X-Triage-Token")
    conversation = _state["intercom"].get_conversation(conversation_id)
    brief = triage(conversation, _state["intercom"], _state["anthropic"])
    store.record(conversation_id, brief, posted=False, repo_heads=repos.heads())
    if brief is None:
        return {"conversation_id": conversation_id, "brief": None}
    return {
        "conversation_id": conversation_id,
        "brief": brief.model_dump(),
        "note_preview": note_html(brief, _state.get("app_id")),
    }

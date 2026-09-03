"""Thin Intercom REST client — only the calls this service actually makes."""

from __future__ import annotations

import hashlib
import hmac
import html
import re

import httpx2 as httpx

from .config import settings

_TAG = re.compile(r"<[^>]+>")
_BREAK = re.compile(r"</p>|<br\s*/?>", re.I)


def strip_html(body: str | None) -> str:
    if not body:
        return ""
    text = _BREAK.sub("\n", body)
    text = _TAG.sub("", text)
    return html.unescape(text).strip()


def verify_signature(raw_body: bytes, header: str | None) -> bool:
    """Intercom signs webhooks as `X-Hub-Signature: sha1=<hmac>` over the raw body."""
    if not settings.intercom_client_secret:
        return settings.allow_unsigned_webhooks
    if not header or not header.startswith("sha1="):
        return False
    expected = hmac.new(
        settings.intercom_client_secret.encode(), raw_body, hashlib.sha1
    ).hexdigest()
    return hmac.compare_digest(expected, header[len("sha1=") :])


class Intercom:
    def __init__(self) -> None:
        headers = {
            "Authorization": f"Bearer {settings.intercom_access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if settings.intercom_api_version:
            headers["Intercom-Version"] = settings.intercom_api_version
        self._http = httpx.Client(
            base_url=settings.intercom_api_base, headers=headers, timeout=30.0
        )

    def close(self) -> None:
        self._http.close()

    def _json(self, resp: httpx.Response) -> dict:
        resp.raise_for_status()
        return resp.json()

    # --- reads -------------------------------------------------------------

    def get_conversation(self, conversation_id: str) -> dict:
        return self._json(
            self._http.get(
                f"/conversations/{conversation_id}", params={"display_as": "plaintext"}
            )
        )

    def search_closed_conversations(self, keywords: list[str], limit: int) -> list[dict]:
        """Keyword search over the *opening message* of closed conversations.

        Intercom's `source.body` filter is word-level `contains` against the first
        message only — it is not semantic search and it does not see replies.
        Callers should pass several distinct keywords.
        """
        keywords = [k.strip() for k in keywords if k.strip()][:10]
        if not keywords:
            return []
        query = {
            "operator": "AND",
            "value": [
                {"field": "state", "operator": "=", "value": "closed"},
                {
                    "operator": "OR",
                    "value": [
                        {"field": "source.body", "operator": "~", "value": kw}
                        for kw in keywords
                    ],
                },
            ],
        }
        body = {"query": query, "pagination": {"per_page": min(limit, 50)}}
        return self._json(
            self._http.post("/conversations/search", json=body)
        ).get("conversations", [])

    def operator_admin_id(self) -> str | None:
        """The workspace's bot admin — notes posted as this don't impersonate a teammate."""
        admins = self._json(self._http.get("/admins")).get("admins", [])
        for admin in admins:
            if "operator" in (admin.get("email") or ""):
                return str(admin["id"])
        return None

    # --- writes ------------------------------------------------------------

    def post_note(self, conversation_id: str, admin_id: str, body_html: str) -> dict:
        """Internal note — visible to teammates only, never to the customer."""
        return self._json(
            self._http.post(
                f"/conversations/{conversation_id}/reply",
                json={
                    "message_type": "note",
                    "type": "admin",
                    "admin_id": admin_id,
                    "body": body_html,
                },
            )
        )


def transcript(conversation: dict, max_parts: int = 40) -> list[dict]:
    """Flatten a conversation into ordered {author, type, body} entries."""
    out: list[dict] = []
    source = conversation.get("source") or {}
    author = source.get("author") or {}
    out.append(
        {
            "author": author.get("name") or author.get("type") or "customer",
            "type": author.get("type", "user"),
            "body": strip_html(source.get("body")),
        }
    )
    parts = (conversation.get("conversation_parts") or {}).get("conversation_parts", [])
    for part in parts[:max_parts]:
        if part.get("part_type") not in ("comment", "note", "assignment"):
            continue
        body = strip_html(part.get("body"))
        if not body:
            continue
        pauthor = part.get("author") or {}
        out.append(
            {
                "author": pauthor.get("name") or pauthor.get("type") or "unknown",
                "type": pauthor.get("type", "unknown"),
                "body": body,
            }
        )
    return out


CUSTOMER_TYPES = {"user", "lead", "contact"}


def customer_replies(conversation: dict) -> list[str]:
    """Customer comments posted *after* the opening message."""
    out = []
    for part in (conversation.get("conversation_parts") or {}).get("conversation_parts", []):
        if part.get("part_type") != "comment":
            continue
        if (part.get("author") or {}).get("type") not in CUSTOMER_TYPES:
            continue
        body = strip_html(part.get("body"))
        if body:
            out.append(body)
    return out


def awaiting_customer_detail(conversation: dict, intake_labels: set[str]) -> bool:
    """True when the opening message is a Messenger workflow selection and the
    customer has not yet typed their actual question.

    See app/intake.py for how intake_labels is derived.
    """
    if customer_replies(conversation):
        return False
    opening = " ".join(
        strip_html((conversation.get("source") or {}).get("body")).split()
    ).strip().lower()
    if not opening:
        return True
    # Prefix-tolerant: the same Messenger button reaches the API both as
    # "Question about Flox for my company" and with its "(paid customers,
    # pricing, enterprise)" suffix. Exact matching misses one of them.
    for label in intake_labels:
        if len(opening) >= 20 and (opening.startswith(label[:40]) or label.startswith(opening[:40])):
            return True
    return False


def customer_fingerprint(conversation: dict) -> str:
    """Hash of everything the customer has said so far.

    Dedupe keys on this rather than on the conversation id: if a brief was
    produced before the customer's real question landed, the new question changes
    the fingerprint and the conversation becomes eligible for a fresh brief.
    Keying on id alone means one premature run silently blocks the real one.
    """
    opening = strip_html((conversation.get("source") or {}).get("body"))
    parts = [opening, *customer_replies(conversation)]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]

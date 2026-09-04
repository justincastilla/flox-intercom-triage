import json
from functools import lru_cache
from pathlib import Path
from typing import ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Intercom ---
    intercom_access_token: str = ""
    intercom_client_secret: str = ""
    intercom_api_base: str = "https://api.intercom.io"
    # Leave empty to use the workspace's default API version.
    intercom_api_version: str = ""
    # Refuse unsigned webhooks unless this is explicitly turned on for local dev.
    allow_unsigned_webhooks: bool = False

    # --- Claude ---
    triage_model: str = "claude-opus-5"
    triage_effort: str = "high"
    triage_max_tokens: int = 16000

    # --- Retrieval ---
    # Colon-separated local checkouts to search with ripgrep. Left empty in the
    # cloud, where repo_dir clones are used instead.
    doc_roots: str = ""
    # Where cloned source repos live. Must be on a writable, ideally persistent path.
    repo_dir: str = "./repos"
    repo_sync_minutes: int = 60
    # Published llms.txt corpora, fetched instead of cloning the website repo.
    # /docs/llms-full.txt is deliberately excluded: it duplicates the flox/docs
    # clone, which carries line numbers. Colon-separated to add more.
    llms_urls: str = "https://flox.dev/llms-full.txt"
    # Read-only PAT or app token, needed only for private repos.
    github_token: str = ""
    # JSON list of repos to clone: name, url, branch, optional sparse paths.
    repos_json: str = ""
    past_ticket_limit: int = 8

    # --- Behaviour ---
    # Shadow mode is the default: compute the brief, store it, post nothing.
    post_notes: bool = False
    min_confidence: float = 0.55

    # --- Spend limits ---
    # One brief is ~14 sequential Opus calls; the Messenger is public.
    max_concurrent_triage: int = 3
    triage_queue_timeout_seconds: int = 240
    conversation_cooldown_seconds: int = 120
    max_briefs_per_conversation: int = 5
    max_triage_runs_per_hour: int = 40

    # Typing this in an internal note re-runs triage on demand. An explicit human
    # request bypasses shadow mode and the confidence gates — a command that
    # silently does nothing reads as broken.
    triage_command: str = "/gnome"
    # Also honour the agent's own judgement that a note would add nothing.
    require_worth_posting: bool = True
    brief_log_path: str = "briefs.jsonl"
    database_url: str = ""
    gap_report_path: str = "doc-gaps.md"
    intake_cache_path: str = "intake_labels.json"
    # Shared secret for the manual /triage endpoint. Unset = endpoint disabled.
    # It runs the model and returns ticket content, so it must not be open on a
    # publicly reachable tunnel.
    triage_admin_token: str = ""

    # Public by default; floxwebsite is private and 1.4G, so only its content
    # directories are sparse-checked-out and it is skipped without a token.
    DEFAULT_REPOS: ClassVar[list[dict]] = [
        {"name": "docs", "url": "https://github.com/flox/docs", "branch": "main"},
        {"name": "flox", "url": "https://github.com/flox/flox", "branch": "main"},
    ]

    def repo_list(self) -> list[dict]:
        repos = json.loads(self.repos_json) if self.repos_json else self.DEFAULT_REPOS
        return [r for r in repos if not r.get("private") or self.github_token]

    def llms_url_list(self) -> list[str]:
        return [u.strip() for u in self.llms_urls.split(":::") if u.strip()]

    def doc_root_list(self) -> list[str]:
        """Search roots: explicit local checkouts if configured, else the clones."""
        if self.doc_roots.strip():
            return [p for p in self.doc_roots.split(":") if p.strip()]
        base = Path(self.repo_dir)
        roots = [str(base / r["name"]) for r in self.repo_list() if (base / r["name"]).is_dir()]
        if (base / "llms").is_dir():
            roots.append(str(base / "llms"))
        return roots


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

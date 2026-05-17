"""Claude authentication.

Primary path is the user's Claude login (the OAuth credentials the Claude CLI
writes to ``~/.claude/.credentials.json``). If they are not logged in, an
``ANTHROPIC_API_KEY`` environment variable is still honoured as a fallback.
Nothing here ever prompts interactively; it only reads existing credentials.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

_CREDENTIALS = Path.home() / ".claude" / ".credentials.json"

LOGIN_HINT = (
    "Not logged in to Claude. Run `claude login` (or `claude setup-token`) and "
    "re-run, or pass --no-llm to skip AI enrichment."
)


@dataclass
class ClaudeAuth:
    token: str
    kind: str  # "oauth" | "api_key"


def _from_cli_login() -> ClaudeAuth | None:
    try:
        data = json.loads(_CREDENTIALS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    oauth = data.get("claudeAiOauth") or {}
    token = oauth.get("accessToken")
    if not token:
        return None
    expires_at = oauth.get("expiresAt")
    if isinstance(expires_at, (int, float)) and expires_at > 0:
        if time.time() * 1000 >= expires_at:
            return None  # session expired -> caller should ask to re-login
    return ClaudeAuth(token=token, kind="oauth")


def _from_env() -> ClaudeAuth | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    return ClaudeAuth(token=key, kind="api_key") if key else None


def resolve_auth() -> ClaudeAuth | None:
    """Return Claude credentials from the CLI login, else the env key, else None."""
    return _from_cli_login() or _from_env()

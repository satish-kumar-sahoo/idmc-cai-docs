import json
import time

from cai_docs import auth


def _write_creds(path, token="sk-ant-oat01-abc", expires_in_ms=3_600_000):
    path.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": token,
                    "refreshToken": "r",
                    "expiresAt": int(time.time() * 1000) + expires_in_ms,
                    "scopes": ["user:inference"],
                }
            }
        ),
        encoding="utf-8",
    )


def test_uses_claude_cli_login(tmp_path, monkeypatch):
    creds = tmp_path / ".credentials.json"
    _write_creds(creds)
    monkeypatch.setattr(auth, "_CREDENTIALS", creds)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    a = auth.resolve_auth()
    assert a is not None
    assert a.kind == "oauth"
    assert a.token == "sk-ant-oat01-abc"


def test_expired_login_is_ignored(tmp_path, monkeypatch):
    creds = tmp_path / ".credentials.json"
    _write_creds(creds, expires_in_ms=-1000)  # already expired
    monkeypatch.setattr(auth, "_CREDENTIALS", creds)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert auth.resolve_auth() is None


def test_env_key_is_fallback_when_not_logged_in(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "_CREDENTIALS", tmp_path / "missing.json")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-key")

    a = auth.resolve_auth()
    assert a is not None
    assert a.kind == "api_key"
    assert a.token == "sk-ant-key"


def test_cli_login_takes_priority_over_env(tmp_path, monkeypatch):
    creds = tmp_path / ".credentials.json"
    _write_creds(creds, token="oauth-wins")
    monkeypatch.setattr(auth, "_CREDENTIALS", creds)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-key")

    a = auth.resolve_auth()
    assert a.kind == "oauth"
    assert a.token == "oauth-wins"


def test_nothing_available(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "_CREDENTIALS", tmp_path / "missing.json")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert auth.resolve_auth() is None

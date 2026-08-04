"""Pluggable entry-layer auth: mode/provider, session vs api_key, no pipeline hard-bind."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.auth.config import AuthConfig, load_auth_config, save_auth_config
from src.auth.errors import AuthError, LoginRequiredError
from src.auth.registry import get_provider, list_provider_ids
from src.auth.resolver import resolve_credential, status
from src.auth.session_store import clear_session, save_session


@pytest.fixture(autouse=True)
def _isolate_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("LUOXIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LUOXIA_AUTH_MODE", raising=False)
    monkeypatch.delenv("LUOXIA_AUTH_PROVIDER", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # Never pick up the developer's real ~/.Doggy/auth.json during unit tests.
    monkeypatch.setenv("DOGGY_AUTH_JSON", str(tmp_path / "no-doggy-auth.json"))
    # Reset registry builtins flag so providers re-bind cleanly if needed
    import src.auth.registry as reg

    reg._BUILTINS_LOADED = False
    reg._REGISTRY.clear()
    yield
    # Leave no sticky auth env for subsequent suites (e.g. tests/luoxia)
    os.environ.pop("LUOXIA_AUTH_MODE", None)
    os.environ.pop("LUOXIA_AUTH_PROVIDER", None)
    os.environ.pop("LUOXIA_DATA_DIR", None)


def test_default_mode_is_session_and_provider_listed():
    cfg = load_auth_config()
    assert cfg.mode == "session"
    assert "xai_pool" in list_provider_ids()
    assert "api_key_bundle" in list_provider_ids()
    assert "offline" in list_provider_ids()


def test_switch_provider_via_config_only():
    save_auth_config(AuthConfig(mode="session", provider="xai_pool"))
    cfg = load_auth_config()
    assert cfg.provider == "xai_pool"
    # Another registered id (offline adapter still resolvable as provider class)
    save_auth_config(AuthConfig(mode="offline", provider="offline"))
    cfg2 = load_auth_config()
    assert cfg2.mode == "offline"


def test_session_mode_without_login_raises_login_required_not_api_key():
    save_auth_config(AuthConfig(mode="session", provider="xai_pool"))
    with pytest.raises(LoginRequiredError) as ei:
        resolve_credential()
    msg = str(ei.value).lower()
    assert "login" in msg or "session" in msg
    assert "dashscope_api_key" not in msg


def test_session_login_token_then_resolve():
    save_auth_config(AuthConfig(mode="session", provider="xai_pool"))
    p = get_provider("xai_pool")
    p.login({"action": "token", "access_token": "test-session-token-xyz", "email": "u@test"})
    cred = resolve_credential()
    assert cred.credential.kind == "session"
    assert cred.credential.token == "test-session-token-xyz"
    st = status()
    assert st.signed_in is True
    assert st.label == "u@test"
    p.logout()
    with pytest.raises(LoginRequiredError):
        resolve_credential()


def test_grok_login_imports_cli_session(tmp_path, monkeypatch):
    doggy = tmp_path / "auth.json"
    doggy.write_text(
        json.dumps(
            {
                "https://auth.x.ai::client": {
                    "key": "grok-access-token",
                    "refresh_token": "r",
                    "email": "grok@test",
                    "expires_at": "2099-01-01T00:00:00+00:00",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DOGGY_AUTH_JSON", str(doggy))
    save_auth_config(AuthConfig(mode="session", provider="xai_pool"))
    p = get_provider("xai_pool")
    p.login({"action": "grok_login"})
    cred = resolve_credential()
    assert cred.credential.token == "grok-access-token"


def test_api_key_mode_uses_env_not_session():
    save_auth_config(AuthConfig(mode="api_key", provider="xai_pool"))
    os.environ["XAI_API_KEY"] = "xai-payg-key"
    cred = resolve_credential()
    assert cred.mode == "api_key"
    assert cred.credential.kind == "api_key"
    assert cred.credential.token == "xai-payg-key"


def test_offline_mode_blocks_cloud():
    save_auth_config(AuthConfig(mode="offline", provider="offline"))
    with pytest.raises(AuthError) as ei:
        resolve_credential()
    assert ei.value.code == "offline"


def test_grok_headers_message_mentions_login_when_session_empty(monkeypatch):
    save_auth_config(AuthConfig(mode="session", provider="xai_pool"))
    clear_session("xai_pool")
    from src.models.grok import GrokVideoModel

    m = GrokVideoModel({})
    with pytest.raises(RuntimeError) as ei:
        m._headers()
    assert "login" in str(ei.value).lower() or "session" in str(ei.value).lower()
    assert "XAI_API_KEY is not configured" not in str(ei.value)

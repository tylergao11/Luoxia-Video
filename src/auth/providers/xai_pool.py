"""xAI subscription-pool session adapter (default pool provider — swappable).

Login options (no pay-per-call API key paste required for session mode):
1. Grok login — reuse Grok CLI session from ~/.Doggy/auth.json (same OAuth store)
2. Paste an OAuth access_token (+ optional refresh_token) into local session store
3. Device/OAuth full browser flow can be added later without changing pipeline

This is one AuthProvider implementation. Switching away = change LUOXIA_AUTH_PROVIDER.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from src.auth.base import Credential
from src.auth.errors import AuthError, LoginRequiredError
from src.auth.session_store import clear_session, load_session, save_session

_PROVIDER_ID = "xai_pool"
_DEFAULT_BASE = "https://api.x.ai/v1"


def _doggy_auth_path() -> Path:
    override = os.getenv("DOGGY_AUTH_JSON") or os.getenv("GROK_AUTH_JSON")
    if override:
        return Path(override)
    return Path.home() / ".Doggy" / "auth.json"


def _parse_doggy_auth(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict) or not raw:
        return None
    # Doggy shape: { "https://auth.x.ai::client_id": { key, refresh_token, expires_at, email, ... } }
    best = None
    best_exp = 0.0
    for _k, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        token = entry.get("key") or entry.get("access_token")
        if not token:
            continue
        exp_raw = entry.get("expires_at")
        exp_ts = _parse_expiry(exp_raw)
        if best is None or exp_ts >= best_exp:
            best = entry
            best_exp = exp_ts
    if not best:
        return None
    return {
        "access_token": best.get("key") or best.get("access_token"),
        "refresh_token": best.get("refresh_token"),
        "expires_at": best.get("expires_at"),
        "email": best.get("email"),
        "user_id": best.get("user_id") or best.get("principal_id"),
        "source": "doggy_auth_json",
        "oidc_issuer": best.get("oidc_issuer"),
        "oidc_client_id": best.get("oidc_client_id"),
    }


def _parse_expiry(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return 0.0
    # ISO-8601
    try:
        from datetime import datetime

        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


def _session_expired(session: Dict[str, Any], *, skew_s: float = 60.0) -> bool:
    exp = _parse_expiry(session.get("expires_at"))
    if exp <= 0:
        # No expiry recorded — treat as valid for a grace window after save
        return False
    return time.time() >= (exp - skew_s)


class XaiPoolAuthProvider:
    id = _PROVIDER_ID
    display_name = "Grok subscription pool (session)"

    def status(self) -> Dict[str, Any]:
        session = self._load_effective_session()
        if not session or not session.get("access_token"):
            grok_store = _doggy_auth_path()
            return {
                "signed_in": False,
                "label": None,
                "message": (
                    "Need Grok login for subscription pool. "
                    f"Click Grok login (session at {grok_store}) or paste access_token — not an API key."
                ),
                "grok_auth_present": grok_store.is_file(),
                "doggy_auth_present": grok_store.is_file(),  # legacy field
            }
        expired = _session_expired(session)
        return {
            "signed_in": not expired,
            "label": session.get("email") or session.get("user_id") or "session",
            "message": "Session expired — Grok login again" if expired else "Signed in via Grok",
            "source": session.get("source"),
            "expires_at": session.get("expires_at"),
            "expired": expired,
        }

    def login(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = payload or {}
        action = (payload.get("action") or "grok_login").strip().lower()

        if action in ("grok_login", "grok", "import_doggy", "import", "sync"):
            imported = _parse_doggy_auth(_doggy_auth_path())
            if not imported or not imported.get("access_token"):
                raise LoginRequiredError(
                    f"No Grok session at {_doggy_auth_path()}. "
                    "Run `grok login` first, then click Grok login here "
                    "(or pass action=token with access_token)."
                )
            imported["source"] = "grok_login"
            save_session(self.id, imported)
            return self.status()

        if action in ("token", "paste", "session"):
            token = (payload.get("access_token") or payload.get("token") or "").strip()
            if not token:
                raise AuthError("access_token required for token login", code="bad_login_payload")
            session = {
                "access_token": token,
                "refresh_token": (payload.get("refresh_token") or "").strip() or None,
                "expires_at": payload.get("expires_at"),
                "email": payload.get("email"),
                "user_id": payload.get("user_id"),
                "source": "pasted_token",
            }
            save_session(self.id, session)
            return self.status()

        raise AuthError(
            f"Unknown login action '{action}'. Use grok_login or token.",
            code="bad_login_payload",
        )

    def logout(self) -> None:
        clear_session(self.id)

    def resolve(self) -> Credential:
        session = self._load_effective_session()
        if not session or not session.get("access_token"):
            raise LoginRequiredError(
                "Need Grok login for subscription pool (not an API key). "
                "POST /auth/login with action=grok_login or access_token."
            )
        if _session_expired(session):
            raise LoginRequiredError(
                "Subscription pool session expired — Grok login again or paste token."
            )
        token = session["access_token"]
        base = (os.getenv("XAI_API_BASE") or _DEFAULT_BASE).rstrip("/")
        return Credential(
            token=token,
            kind="session",
            provider=self.id,
            base_url=base,
            headers={"Authorization": f"Bearer {token}"},
            meta={
                "email": session.get("email"),
                "source": session.get("source"),
            },
        )

    def _load_effective_session(self) -> Optional[Dict[str, Any]]:
        local = load_session(self.id)
        if local and local.get("access_token") and not _session_expired(local):
            return local
        # Live import from Doggy if local missing/expired
        imported = _parse_doggy_auth(_doggy_auth_path())
        if imported and imported.get("access_token") and not _session_expired(imported):
            # Cache a copy so status is stable
            try:
                save_session(self.id, imported)
            except Exception:
                pass
            return imported
        return local if local and local.get("access_token") else imported

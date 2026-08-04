"""Single entry for credential resolution used by model clients."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from .base import Credential
from .config import AuthConfig, load_auth_config
from .errors import AuthError, LoginRequiredError
from .registry import get_provider, list_providers


@dataclass
class ResolvedCredential:
    credential: Credential
    mode: str
    provider: str


@dataclass
class AuthStatus:
    mode: str
    provider: str
    signed_in: bool
    label: Optional[str]
    message: str
    providers: list
    detail: Dict[str, Any]


def resolve_credential(
    *,
    config: Optional[AuthConfig] = None,
    purpose: str = "generation",
) -> ResolvedCredential:
    """Resolve how this process pays for cloud generation.

    - session: active pool adapter must return a session token (LoginRequiredError if not)
    - api_key: adapter resolves long-lived env keys
    - offline: raises AuthError with code offline (callers must skip cloud, not fake it)
    """
    cfg = config or load_auth_config()
    if cfg.mode == "offline":
        raise AuthError(
            "Auth mode is offline — cloud generation disabled. Log in or switch to api_key mode.",
            code="offline",
        )

    # Map mode to which adapter owns resolution.
    if cfg.mode == "api_key":
        provider = get_provider("api_key_bundle")
    else:
        provider = get_provider(cfg.provider)

    try:
        cred = provider.resolve()
    except LoginRequiredError:
        raise
    except AuthError:
        raise
    except Exception as e:
        raise AuthError(str(e), code="resolve_failed") from e

    return ResolvedCredential(credential=cred, mode=cfg.mode, provider=cfg.provider)


def status() -> AuthStatus:
    cfg = load_auth_config()
    signed_in = False
    label = None
    message = ""
    detail: Dict[str, Any] = {}

    try:
        if cfg.mode == "offline":
            message = "Offline mode — no cloud credentials required."
            detail = {"code": "offline"}
        elif cfg.mode == "api_key":
            p = get_provider("api_key_bundle")
            st = p.status()
            signed_in = bool(st.get("signed_in"))
            label = st.get("label")
            message = st.get("message") or ("API keys configured" if signed_in else "API keys missing")
            detail = st
        else:
            p = get_provider(cfg.provider)
            st = p.status()
            signed_in = bool(st.get("signed_in"))
            label = st.get("label")
            if signed_in:
                message = st.get("message") or f"Signed in via {cfg.provider}"
            else:
                message = st.get("message") or "Need login for subscription pool (not an API key)."
            detail = st
    except AuthError as e:
        message = str(e)
        detail = {"code": e.code}

    return AuthStatus(
        mode=cfg.mode,
        provider=cfg.provider,
        signed_in=signed_in,
        label=label,
        message=message,
        providers=list_providers(),
        detail=detail,
    )


# Alias type name used by package exports
AuthStatus  # noqa: B018 — re-export clarity

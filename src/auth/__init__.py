"""Pluggable entry-layer auth: subscription pool session vs API key vs offline.

Pipeline / beats / timeline code should call ``resolve_credential`` only —
never import a single vendor's login flow.
"""
from .config import AuthConfig, load_auth_config, save_auth_config
from .errors import AuthError, LoginRequiredError
from .resolver import AuthStatus, ResolvedCredential, resolve_credential, status as auth_status
from .session_store import clear_session, load_session, save_session

__all__ = [
    "AuthConfig",
    "AuthError",
    "AuthStatus",
    "LoginRequiredError",
    "ResolvedCredential",
    "auth_status",
    "clear_session",
    "load_auth_config",
    "load_session",
    "resolve_credential",
    "save_auth_config",
    "save_session",
]

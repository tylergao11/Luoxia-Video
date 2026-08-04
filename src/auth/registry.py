"""Auth provider registry — add a new pool by registering an adapter, not forking pipeline."""
from __future__ import annotations

import threading
from typing import Dict, List, Type

from .base import AuthProvider
from .errors import AuthError

_REGISTRY: Dict[str, AuthProvider] = {}
_FACTORY: Dict[str, Type] = {}


def register_provider(provider: AuthProvider) -> None:
    pid = getattr(provider, "id", None)
    if not pid:
        raise AuthError("auth provider missing id")
    _REGISTRY[str(pid)] = provider


def register_provider_class(provider_id: str, cls: Type) -> None:
    _FACTORY[provider_id] = cls


def get_provider(provider_id: str) -> AuthProvider:
    ensure_builtin_providers()
    if provider_id in _REGISTRY:
        return _REGISTRY[provider_id]
    if provider_id in _FACTORY:
        inst = _FACTORY[provider_id]()
        _REGISTRY[provider_id] = inst
        return inst
    raise AuthError(
        f"Unknown auth provider '{provider_id}'. "
        f"Known: {', '.join(sorted(list_provider_ids())) or '(none)'}",
        code="unknown_provider",
    )


def list_provider_ids() -> List[str]:
    ensure_builtin_providers()
    ids = set(_REGISTRY) | set(_FACTORY)
    return sorted(ids)


def list_providers() -> List[Dict[str, str]]:
    ensure_builtin_providers()
    out = []
    for pid in list_provider_ids():
        p = get_provider(pid)
        out.append({"id": p.id, "display_name": getattr(p, "display_name", p.id)})
    return out


_BUILTINS_LOADED = False
_BUILTINS_LOCK = threading.Lock()


def ensure_builtin_providers() -> None:
    global _BUILTINS_LOADED
    if _BUILTINS_LOADED:
        return
    with _BUILTINS_LOCK:
        if _BUILTINS_LOADED:
            return
        # Set the ready flag only after every class is registered.  Setting it before
        # lazy imports let a concurrent TTS request observe an empty registry and fail
        # with "Known: (none)" while the first request was still importing providers.
        from .providers.xai_pool import XaiPoolAuthProvider
        from .providers.api_key_bundle import ApiKeyBundleProvider
        from .providers.offline import OfflineAuthProvider

        register_provider_class("xai_pool", XaiPoolAuthProvider)
        register_provider_class("api_key_bundle", ApiKeyBundleProvider)
        register_provider_class("offline", OfflineAuthProvider)
        _BUILTINS_LOADED = True

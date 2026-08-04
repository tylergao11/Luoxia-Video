"""Alternate mode: long-lived vendor API keys from environment."""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from src.auth.base import Credential
from src.auth.errors import AuthError


class ApiKeyBundleProvider:
    id = "api_key_bundle"
    display_name = "Direct API keys (env)"

    def status(self) -> Dict[str, Any]:
        keys = {
            "DASHSCOPE_API_KEY": bool(os.getenv("DASHSCOPE_API_KEY")),
            "XAI_API_KEY": bool(os.getenv("XAI_API_KEY")),
            "OPENAI_API_KEY": bool(os.getenv("OPENAI_API_KEY")),
        }
        any_set = any(keys.values())
        return {
            "signed_in": any_set,
            "label": "api_key" if any_set else None,
            "message": "At least one vendor API key is set" if any_set else "No vendor API keys in environment",
            "keys_present": keys,
        }

    def login(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # Keys are configured via settings/.env — not login.
        return self.status()

    def logout(self) -> None:
        return None

    def resolve(self) -> Credential:
        # Prefer XAI then OpenAI then DashScope for a generic bearer; callers that
        # need a specific vendor still read env for their own key after mode check.
        for env_name in ("XAI_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY"):
            val = (os.getenv(env_name) or "").strip()
            if val:
                return Credential(
                    token=val,
                    kind="api_key",
                    provider=self.id,
                    meta={"env": env_name},
                )
        raise AuthError(
            "API-key mode active but no DASHSCOPE_API_KEY / XAI_API_KEY / OPENAI_API_KEY set.",
            code="api_key_missing",
        )

from __future__ import annotations

from typing import Any, Dict, Optional

from src.auth.base import Credential
from src.auth.errors import AuthError


class OfflineAuthProvider:
    id = "offline"
    display_name = "Offline (no cloud)"

    def status(self) -> Dict[str, Any]:
        return {
            "signed_in": True,
            "label": "offline",
            "message": "Cloud generation disabled",
        }

    def login(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.status()

    def logout(self) -> None:
        return None

    def resolve(self) -> Credential:
        raise AuthError("Offline mode — no cloud credential", code="offline")

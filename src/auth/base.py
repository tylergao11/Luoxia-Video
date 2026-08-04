from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol


@dataclass
class Credential:
    """Resolved bearer/material for one outbound cloud call."""

    token: str
    kind: str  # "session" | "api_key"
    provider: str
    headers: Dict[str, str] = field(default_factory=dict)
    base_url: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    def authorization_header(self) -> str:
        if self.headers.get("Authorization"):
            return self.headers["Authorization"]
        return f"Bearer {self.token}"


class AuthProvider(Protocol):
    """One pluggable pool/login adapter. Register in ``src.auth.registry``."""

    id: str
    display_name: str

    def status(self) -> Dict[str, Any]:
        """Signed-in? email/label? expires? Never include raw tokens."""
        ...

    def login(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Start or complete login. Payload is provider-specific (token paste, device code, …)."""
        ...

    def logout(self) -> None:
        ...

    def resolve(self) -> Credential:
        """Return a usable credential or raise LoginRequiredError / AuthError."""
        ...

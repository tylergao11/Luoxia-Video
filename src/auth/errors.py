from __future__ import annotations


class AuthError(RuntimeError):
    """Base auth failure with a stable machine code for UI mapping."""

    def __init__(self, message: str, *, code: str = "auth_error"):
        super().__init__(message)
        self.code = code


class LoginRequiredError(AuthError):
    """Pool/session mode is active but no usable login session exists."""

    def __init__(self, message: str = "Need login for subscription pool (not an API key)."):
        super().__init__(message, code="login_required")

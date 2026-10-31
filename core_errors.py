"""Exceptions whose message is safe to show to end users (no secrets, no stack traces)."""
from __future__ import annotations


class HopelessError(Exception):
    """Base class for errors with a user-facing message."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


class NotAuthorized(HopelessError):
    """The caller lacks the permission level this action needs."""


class InvalidInput(HopelessError):
    """The caller supplied something we can't use."""


class DatabaseUnavailable(HopelessError):
    def __init__(self, user_message: str = "") -> None:
        import core_messages as messages
        user_message = user_message or messages.no_response()
        super().__init__(user_message)

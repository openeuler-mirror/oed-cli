"""Exit codes and exception hierarchy for ``oed``."""

from __future__ import annotations


class ExitCode:
    OK = 0
    USER_ERROR = 1
    NETWORK_ERROR = 2
    UPSTREAM_ERROR = 3
    NOT_FOUND = 4


class OedError(Exception):
    """Base for all ``oed`` runtime errors."""

    code: int = ExitCode.USER_ERROR

    def __init__(self, message: str, *, kind: str | None = None, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.kind = kind or self.__class__.__name__
        self.hint = hint

    def to_dict(self) -> dict:
        d: dict = {"ok": False, "code": self.code, "error": self.kind, "message": self.message}
        if self.hint:
            d["hint"] = self.hint
        return d


class UserError(OedError):
    code = ExitCode.USER_ERROR


class NetworkError(OedError):
    code = ExitCode.NETWORK_ERROR


class UpstreamError(OedError):
    code = ExitCode.UPSTREAM_ERROR


class NotFoundError(OedError):
    code = ExitCode.NOT_FOUND

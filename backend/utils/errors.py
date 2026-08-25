"""Typed errors. Every failure the API returns is {code, message, detail?,
retryable} -- never a stack trace, never a raw exception string (§12)."""
from __future__ import annotations

from typing import Any


class AppError(Exception):
    code = "INTERNAL"
    http_status = 500
    retryable = False

    def __init__(self, message: str, detail: Any = None, *,
                 code: str | None = None, retryable: bool | None = None,
                 http_status: int | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail
        if code:
            self.code = code
        if retryable is not None:
            self.retryable = retryable
        if http_status is not None:
            self.http_status = http_status

    def as_dict(self) -> dict:
        d = {"code": self.code, "message": self.message, "retryable": self.retryable}
        if self.detail is not None:
            d["detail"] = self.detail
        return d


class NotFound(AppError):
    code, http_status = "NOT_FOUND", 404


class BadRequest(AppError):
    code, http_status = "BAD_REQUEST", 400


class NotAPdf(AppError):
    code, http_status = "NOT_A_PDF", 415


class CorruptPdf(AppError):
    code, http_status = "CORRUPT_PDF", 422


class PasswordProtected(AppError):
    code, http_status = "PASSWORD_PROTECTED", 422


class EmptyDocument(AppError):
    code, http_status = "EMPTY_DOCUMENT", 422


class TooManyPages(AppError):
    code, http_status = "TOO_MANY_PAGES", 413


class FileTooLarge(AppError):
    code, http_status = "FILE_TOO_LARGE", 413


class OcrUnavailable(AppError):
    code, http_status = "OCR_UNAVAILABLE", 503


class ProviderError(AppError):
    code, http_status, retryable = "PROVIDER_ERROR", 502, True


class ProviderResponseError(ProviderError):
    code = "PROVIDER_MALFORMED_RESPONSE"


class ProviderTimeout(ProviderError):
    code = "PROVIDER_TIMEOUT"


class ProviderRateLimited(ProviderError):
    code = "PROVIDER_RATE_LIMITED"


class RenderError(AppError):
    code, http_status = "RENDER_FAILED", 500


class InvariantViolation(AppError):
    """Raised by the exporter when I1/I2 fail. Always a hard failure."""
    code, http_status = "INVARIANT_VIOLATION", 500

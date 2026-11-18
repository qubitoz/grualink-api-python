"""Typed errors raised by the client. One class per row of the error table in the API contract."""

from __future__ import annotations

from typing import TypedDict


class ErrorDetail(TypedDict):
    """One entry of ``error.details`` in a 400/422 response."""

    #: Dotted path of the offending field, e.g. ``contact.phone``.
    field: str
    #: One of: required, invalid_type, too_long, invalid_format, out_of_range, unknown_field,
    #: invalid_value, duplicate.
    issue: str


class GrualinkError(Exception):
    """Base class of every error raised by this library."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
        #: Number of HTTP attempts made before giving up (0 when no request was sent).
        self.attempts = 0


class GrualinkConfigError(GrualinkError):
    """The client was configured or called with an invalid argument. Nothing was sent."""


class GrualinkConnectionError(GrualinkError):
    """The request never got an HTTP response (DNS, connection reset, TLS...). Retried automatically."""


class GrualinkTimeoutError(GrualinkError):
    """No response within the configured timeout. Retried automatically."""


class GrualinkApiError(GrualinkError):
    """The API answered with an error.

    ``code`` is the stable machine-readable code from ``{"error": {"code", "message", "details"?}}``;
    ``message`` may change over time. When the body is not the documented error shape, ``code`` is
    ``unexpected_response``.
    """

    def __init__(
        self,
        *,
        status: int,
        code: str,
        message: str,
        details: list[ErrorDetail] | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.details: list[ErrorDetail] = details if details is not None else []
        #: Value of the ``Retry-After`` header in seconds, when present.
        self.retry_after_seconds = retry_after_seconds


class GrualinkInvalidRequestError(GrualinkApiError):
    """400, 405, 413, 415: the request is malformed. Fix the request; do not retry."""


class GrualinkAuthenticationError(GrualinkApiError):
    """401 ``unauthorized``: key missing, invalid, expired or revoked (always the same answer)."""


class GrualinkPermissionError(GrualinkApiError):
    """403: the key is not allowed to do this (scope, customer unavailable, live mode, browser)."""


class GrualinkNotFoundError(GrualinkApiError):
    """404 ``not_found``: it does not exist or does not belong to your key."""


class GrualinkIdempotencyConflictError(GrualinkApiError):
    """409 ``idempotency_conflict``: the Idempotency-Key was already used with a different body."""


class GrualinkValidationError(GrualinkApiError):
    """422 ``validation_error``: see ``details`` for the invalid fields."""


class GrualinkRateLimitError(GrualinkApiError):
    """429 ``rate_limited`` (retried automatically) or ``too_many_open_requests`` (not retried)."""


class GrualinkServerError(GrualinkApiError):
    """5xx: retried automatically; safe because the same Idempotency-Key is reused."""


def api_error_from_status(
    *,
    status: int,
    code: str,
    message: str,
    details: list[ErrorDetail] | None = None,
    retry_after_seconds: int | None = None,
) -> GrualinkApiError:
    """Maps an HTTP status to the error class that represents it."""
    cls: type[GrualinkApiError]
    if status == 401:
        cls = GrualinkAuthenticationError
    elif status == 403:
        cls = GrualinkPermissionError
    elif status == 404:
        cls = GrualinkNotFoundError
    elif status == 409:
        cls = GrualinkIdempotencyConflictError
    elif status == 422:
        cls = GrualinkValidationError
    elif status == 429:
        cls = GrualinkRateLimitError
    elif status >= 500:
        cls = GrualinkServerError
    elif status in (400, 405, 413, 415):
        cls = GrualinkInvalidRequestError
    else:
        cls = GrualinkApiError
    return cls(
        status=status,
        code=code,
        message=message,
        details=details,
        retry_after_seconds=retry_after_seconds,
    )

"""Minimal client for the GRUALINK API, built on the standard library only."""

from __future__ import annotations

import http.client
import json
import math
import random as _random
import re
import ssl
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, NamedTuple, cast
from urllib.parse import quote, urlencode, urlsplit

from .errors import (
    ErrorDetail,
    GrualinkApiError,
    GrualinkConfigError,
    GrualinkConnectionError,
    GrualinkError,
    GrualinkRateLimitError,
    GrualinkServerError,
    GrualinkTimeoutError,
    api_error_from_status,
)
from .types import Health, Mode, ServiceRequest, ServiceRequestInput

DEFAULT_BASE_URL = "https://grualink.mx/api/v1"
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BASE_DELAY_SECONDS = 0.5
DEFAULT_RETRY_MAX_DELAY_SECONDS = 8.0

_API_KEY = re.compile(r"glk_(test|live)_[\x21-\x7e]+")
_IDEMPOTENCY_KEY = re.compile(r"[\x21-\x7e]{1,255}")
_RETRY_AFTER_SECONDS = re.compile(r"[0-9]{1,9}")
_MAX_BODY_BYTES = 16 * 1024
# The API never asks to wait more than 60 seconds.
_MAX_RETRY_AFTER_SECONDS = 60
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
_REDACTED = "[REDACTED]"


class TransportResponse(NamedTuple):
    """What a transport returns: the status, the headers with lowercase names and the raw body."""

    status: int
    headers: Mapping[str, str]
    body: bytes


#: ``(method, url, headers, body, timeout_seconds) -> TransportResponse``. It must not follow redirects.
#: It raises ``TimeoutError`` when the time is up and any other exception when there is no response.
Transport = Callable[[str, str, Mapping[str, str], "bytes | None", float], TransportResponse]


@dataclass(frozen=True)
class RetryInfo:
    """Passed to ``on_retry`` before each wait."""

    #: Attempt that just failed, starting at 1.
    attempt: int
    #: Time the client will wait before the next attempt.
    delay_seconds: float
    #: Why it failed. Never contains the API key.
    error: GrualinkError


@dataclass(frozen=True)
class CreateServiceRequestResult:
    service_request: ServiceRequest
    #: ``True`` when the API answered 200 with ``Idempotent-Replayed: true`` (nothing new was created).
    replayed: bool
    #: The key that was sent (yours or the generated one).
    idempotency_key: str


class _Secret:
    """Holds the API key so that it never shows up when the client or a traceback is printed."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return _REDACTED

    __str__ = __repr__

    def __reduce__(self) -> str:
        raise TypeError("the API key is not serializable")


def default_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout_seconds: float,
) -> TransportResponse:
    """Sends one request with ``http.client``, which never follows redirects.

    ``timeout_seconds`` bounds every network operation and the reading of the whole response.
    """
    parts = urlsplit(url)
    host = parts.hostname or ""
    deadline = time.monotonic() + timeout_seconds
    connection: http.client.HTTPConnection
    if parts.scheme == "https":
        connection = http.client.HTTPSConnection(
            host, parts.port, timeout=timeout_seconds, context=ssl.create_default_context()
        )
    else:
        connection = http.client.HTTPConnection(host, parts.port, timeout=timeout_seconds)
    target = parts.path + (f"?{parts.query}" if parts.query else "")
    try:
        connection.request(method, target, body=body, headers={**headers, "Connection": "close"})
        sock = connection.sock
        response = connection.getresponse()
        chunks: list[bytes] = []
        # The response closes itself once its last byte has been read.
        while not response.isclosed():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("deadline exceeded")
            if sock is not None:
                sock.settimeout(remaining)
            chunk = response.read1(65536)
            if not chunk:
                break
            chunks.append(chunk)
        response_headers = {name.lower(): value for name, value in response.getheaders()}
        return TransportResponse(response.status, response_headers, b"".join(chunks))
    finally:
        connection.close()


def _parse_retry_after(value: str | None, now: datetime) -> int | None:
    if value is None:
        return None
    text = value.strip()
    if _RETRY_AFTER_SECONDS.fullmatch(text):
        return int(text)
    try:
        date = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    return max(0, math.ceil((date - now).total_seconds()))


def _is_retryable(error: GrualinkError) -> bool:
    if isinstance(error, (GrualinkConnectionError, GrualinkTimeoutError, GrualinkServerError)):
        return True
    # too_many_open_requests only clears when the company resolves a request: waiting a few seconds
    # does not help.
    if isinstance(error, GrualinkRateLimitError):
        return error.code != "too_many_open_requests"
    return False


def _positive(value: float | None, fallback: float, name: str) -> float:
    if value is None:
        return fallback
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GrualinkConfigError(f"{name} must be a positive number.")
    if not math.isfinite(value) or value <= 0:
        raise GrualinkConfigError(f"{name} must be a positive number.")
    return float(value)


def _non_negative_int(value: int | None, fallback: int, name: str) -> int:
    if value is None:
        return fallback
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GrualinkConfigError(f"{name} must be a non-negative integer.")
    return value


class GrualinkClient:
    """Client of the GRUALINK API.

    :param api_key: ``glk_test_...`` or ``glk_live_...``. Read it from the environment; never
        hard-code it.
    :param base_url: Defaults to ``https://grualink.mx/api/v1``.
    :param timeout_seconds: Maximum time for each attempt (default 15).
    :param max_retries: Retries after the first attempt for 429 ``rate_limited``, 5xx, timeouts and
        network errors (default 3).
    :param retry_base_delay_seconds: First backoff step (default 0.5).
    :param retry_max_delay_seconds: Ceiling of the exponential backoff (default 8).
    :param on_retry: Called before each wait. Use it for your own logging.
    :param transport: Injection point for tests; see :data:`Transport`.
    :param sleep: Injection point for tests (default ``time.sleep``).
    :param random: Injection point for tests (default ``random.random``).
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        retry_base_delay_seconds: float | None = None,
        retry_max_delay_seconds: float | None = None,
        on_retry: Callable[[RetryInfo], None] | None = None,
        transport: Transport | None = None,
        sleep: Callable[[float], None] | None = None,
        random: Callable[[], float] | None = None,
    ) -> None:
        key = api_key.strip() if isinstance(api_key, str) else ""
        if not key:
            raise GrualinkConfigError("api_key is required (set GRUALINK_API_KEY).")
        if not _API_KEY.fullmatch(key):
            raise GrualinkConfigError("api_key must start with glk_test_ or glk_live_.")

        url = (DEFAULT_BASE_URL if base_url is None else base_url).rstrip("/")
        try:
            parts = urlsplit(url)
            host = parts.hostname
            _ = parts.port
        except (ValueError, AttributeError, TypeError):
            raise GrualinkConfigError("base_url is not a valid URL.") from None
        if not parts.scheme or not host:
            raise GrualinkConfigError("base_url is not a valid URL.")
        if parts.scheme != "https" and not (parts.scheme == "http" and host in _LOCAL_HOSTS):
            raise GrualinkConfigError("base_url must use https (http is only accepted for localhost).")
        if parts.username or parts.password or "?" in url or "#" in url:
            raise GrualinkConfigError("base_url must not contain credentials, query or fragment.")

        # The key lives inside an object that prints as [REDACTED], so it does not show up in repr(),
        # vars() or a traceback that captures local variables.
        self._api_key = _Secret(key)
        self._mode: Mode = "live" if key.startswith("glk_live_") else "test"
        self._base_url = url
        self._timeout_seconds = _positive(timeout_seconds, DEFAULT_TIMEOUT_SECONDS, "timeout_seconds")
        self._max_retries = _non_negative_int(max_retries, DEFAULT_MAX_RETRIES, "max_retries")
        self._retry_base_delay_seconds = _positive(
            retry_base_delay_seconds, DEFAULT_RETRY_BASE_DELAY_SECONDS, "retry_base_delay_seconds"
        )
        self._retry_max_delay_seconds = _positive(
            retry_max_delay_seconds, DEFAULT_RETRY_MAX_DELAY_SECONDS, "retry_max_delay_seconds"
        )
        self._on_retry = on_retry
        self._transport: Transport = transport if transport is not None else default_transport
        self._sleep = sleep if sleep is not None else time.sleep
        self._random = random if random is not None else _random.random

    def __repr__(self) -> str:
        return f"GrualinkClient(mode={self._mode!r}, base_url={self._base_url!r})"

    @property
    def mode(self) -> Mode:
        """``test`` or ``live``, from the key prefix."""
        return self._mode

    def health(self) -> Health:
        """``GET /health``. Sent without the key."""
        _, _, body = self._request("GET", "/health", authenticated=False)
        return cast(Health, body)

    def create_service_request(
        self,
        input: ServiceRequestInput,
        *,
        idempotency_key: str | None = None,
    ) -> CreateServiceRequestResult:
        """``POST /service-requests``.

        The body is serialized once and the exact same bytes and Idempotency-Key are reused on every
        retry, so a retry can never create a second request.

        :param idempotency_key: 1 to 255 visible ASCII characters, unique per request you want to
            create. Store it with your record before calling: sending it again with the same body
            returns the original response instead of a duplicate. Generated (UUID v4) when omitted.
        """
        key = str(uuid.uuid4()) if idempotency_key is None else idempotency_key
        if not isinstance(key, str) or not _IDEMPOTENCY_KEY.fullmatch(key):
            raise GrualinkConfigError("idempotency_key must be 1 to 255 visible ASCII characters.")
        if not isinstance(input, Mapping):
            raise GrualinkConfigError("The service request must be a mapping.")
        try:
            body = json.dumps(input, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode(
                "utf-8"
            )
        except (TypeError, ValueError):
            raise GrualinkConfigError("The service request cannot be serialized as JSON.") from None
        if len(body) > _MAX_BODY_BYTES:
            raise GrualinkConfigError("The service request exceeds 16 KB.")
        status, headers, parsed = self._request(
            "POST",
            "/service-requests",
            body=body,
            headers={"Content-Type": "application/json", "Idempotency-Key": key},
        )
        return CreateServiceRequestResult(
            service_request=cast(ServiceRequest, parsed),
            replayed=status == 200 and headers.get("idempotent-replayed") == "true",
            idempotency_key=key,
        )

    def get_service_request(self, id: str) -> ServiceRequest:
        """``GET /service-requests/{id}``."""
        if not isinstance(id, str) or not id.strip():
            raise GrualinkConfigError("id is required.")
        _, _, body = self._request("GET", f"/service-requests/{quote(id.strip(), safe='')}")
        return cast(ServiceRequest, body)

    def find_service_requests_by_external_ref(self, external_ref: str) -> list[ServiceRequest]:
        """``GET /service-requests?external_ref=...``: up to 20 of your requests, newest first."""
        if not isinstance(external_ref, str) or not external_ref.strip():
            raise GrualinkConfigError("external_ref is required.")
        query = urlencode({"external_ref": external_ref})
        _, _, body = self._request("GET", f"/service-requests?{query}")
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            error = GrualinkApiError(
                status=200,
                code="unexpected_response",
                message="The API answered with a body that is not a list of service requests.",
            )
            error.attempts = 1
            raise error
        return cast("list[ServiceRequest]", data)

    def _redact(self, text: str) -> str:
        """Replaces the API key with a marker in any text that will leave this class."""
        return text.replace(self._api_key.reveal(), _REDACTED)

    def _send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        authenticated: bool,
    ) -> TransportResponse:
        # The Authorization header only exists in this frame, which is never part of the traceback of
        # the errors this class raises.
        if authenticated:
            headers = {**headers, "Authorization": f"Bearer {self._api_key.reveal()}"}
        return self._transport(method, url, headers, body, self._timeout_seconds)

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        authenticated: bool = True,
    ) -> tuple[int, Mapping[str, str], Any]:
        request_headers = {
            "Accept": "application/json",
            "User-Agent": "grualink-api-python-example",
            **(headers or {}),
        }
        url = f"{self._base_url}{path}"

        attempt = 0
        while True:
            attempt += 1
            outcome = self._attempt(method, url, request_headers, body, authenticated)
            if not isinstance(outcome, GrualinkError):
                return outcome
            outcome.attempts = attempt
            if not _is_retryable(outcome) or attempt > self._max_retries:
                raise outcome
            delay_seconds = self._retry_delay(attempt, outcome)
            if self._on_retry is not None:
                self._on_retry(RetryInfo(attempt=attempt, delay_seconds=delay_seconds, error=outcome))
            self._sleep(delay_seconds)

    def _attempt(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        authenticated: bool,
    ) -> tuple[int, Mapping[str, str], Any] | GrualinkError:
        """One HTTP attempt. A failure is returned, not raised.

        The caller raises it from its own frame, so neither the original exception nor the raw
        response (which could echo the key) is attached to the error or to its traceback.
        """
        try:
            response = self._send(method, url, headers, body, authenticated)
        except TimeoutError:
            return GrualinkTimeoutError(
                f"No response from the GRUALINK API within {self._timeout_seconds:g} s."
            )
        except Exception as cause:
            return GrualinkConnectionError(
                self._redact(f"Could not reach the GRUALINK API: {type(cause).__name__}: {cause}")
            )
        if 200 <= response.status < 300:
            try:
                return response.status, response.headers, json.loads(response.body)
            except ValueError:
                return GrualinkApiError(
                    status=response.status,
                    code="unexpected_response",
                    message="The API answered with a body that is not JSON.",
                )
        return self._to_api_error(response)

    def _retry_delay(self, attempt: int, failure: GrualinkError) -> float:
        """Exponential backoff with full jitter; ``Retry-After`` is a floor when the API sends it."""
        ceiling = min(self._retry_max_delay_seconds, self._retry_base_delay_seconds * 2.0 ** (attempt - 1))
        jitter = self._random() * ceiling
        retry_after = failure.retry_after_seconds if isinstance(failure, GrualinkApiError) else None
        if retry_after is None:
            return jitter
        return min(retry_after, _MAX_RETRY_AFTER_SECONDS) + jitter

    def _to_api_error(self, response: TransportResponse) -> GrualinkApiError:
        retry_after_seconds = _parse_retry_after(
            response.headers.get("retry-after"), datetime.now(timezone.utc)
        )
        code = "unexpected_response"
        message = f"The API answered HTTP {response.status}."
        details: list[ErrorDetail] | None = None
        try:
            parsed = json.loads(response.body)
        except ValueError:
            # Not JSON (for example an HTML page from a proxy): keep the generic message; the body is
            # not echoed.
            parsed = None
        error = parsed.get("error") if isinstance(parsed, dict) else None
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            code = self._redact(error["code"])
            if isinstance(error.get("message"), str):
                message = self._redact(error["message"])
            if isinstance(error.get("details"), list):
                details = [
                    {
                        "field": self._redact(str(d.get("field") or "")),
                        "issue": self._redact(str(d.get("issue") or "")),
                    }
                    for d in error["details"]
                    if isinstance(d, dict)
                ]
        return api_error_from_status(
            status=response.status,
            code=code,
            message=message,
            details=details,
            retry_after_seconds=retry_after_seconds,
        )

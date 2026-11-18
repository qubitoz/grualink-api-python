"""Webhook signature verification, deduplication and a framework-independent delivery handler."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

from .types import WebhookEvent

SIGNATURE_HEADER = "x-grualink-signature"
DELIVERY_HEADER = "x-grualink-delivery"
EVENT_HEADER = "x-grualink-event"
TIMESTAMP_HEADER = "x-grualink-timestamp"
MODE_HEADER = "x-grualink-mode"

#: Deliveries whose signed timestamp differs more than this from the current time are rejected.
DEFAULT_TOLERANCE_SECONDS = 300
#: A delivery body is never larger than 64 KB.
MAX_WEBHOOK_BODY_BYTES = 64 * 1024

SignatureVerificationReason = Literal[
    "ok",
    "missing_secret",
    "missing_header",
    "malformed_header",
    "timestamp_out_of_tolerance",
    "signature_mismatch",
]


@dataclass(frozen=True)
class SignatureVerificationResult:
    valid: bool
    reason: SignatureVerificationReason


_TIMESTAMP = re.compile(r"[0-9]{1,12}")
_SIGNATURE_V1 = re.compile(r"[0-9a-f]{64}")


def _fail(reason: SignatureVerificationReason) -> SignatureVerificationResult:
    return SignatureVerificationResult(valid=False, reason=reason)


def _to_bytes(raw_body: bytes | bytearray | memoryview | str) -> bytes:
    return raw_body.encode("utf-8") if isinstance(raw_body, str) else bytes(raw_body)


def compute_webhook_signature(
    secret: str, timestamp: int | str, raw_body: bytes | bytearray | memoryview | str
) -> str:
    """HMAC-SHA256, lowercase hex, of ``<timestamp>.<raw body>`` keyed with the secret as issued."""
    message = f"{timestamp}.".encode() + _to_bytes(raw_body)
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def build_signature_header(
    secrets: Sequence[str], timestamp: int, raw_body: bytes | bytearray | memoryview | str
) -> str:
    """Builds the header the way GRUALINK sends it. Useful to test your own receiver locally."""
    parts = [f"t={timestamp}"]
    parts.extend(f"v1={compute_webhook_signature(s, timestamp, raw_body)}" for s in secrets)
    return ",".join(parts)


def verify_webhook_signature(
    *,
    raw_body: bytes | bytearray | memoryview | str,
    signature_header: str | None,
    secret: str | Sequence[str] | None,
    now_seconds: float | None = None,
    tolerance_seconds: float | None = None,
) -> SignatureVerificationResult:
    """Verifies ``X-GruaLink-Signature``. Pure and total: it never raises and has no side effects.

    :param raw_body: The RAW request body exactly as received, before any JSON parsing. Prefer bytes;
        a string is encoded as UTF-8.
    :param signature_header: Value of the ``X-GruaLink-Signature`` header.
    :param secret: Your signing secret, whole (with its ``whsec_`` prefix). Pass two during your own
        rotation window.
    :param now_seconds: Current time in seconds since 1970 (UTC). Defaults to the system clock.
    :param tolerance_seconds: Defaults to 300 seconds.

    Order of checks (shared by every language; the contract vectors pin it):

    1. no secret                                       -> ``missing_secret``
    2. header absent or blank                          -> ``missing_header``
    3. no ``t`` of 1-12 digits, or no well-formed ``v1``  -> ``malformed_header``
    4. ``|now - t|`` greater than the tolerance        -> ``timestamp_out_of_tolerance``
    5. no ``v1`` equals the expected signature         -> ``signature_mismatch``
    """
    try:
        if isinstance(secret, str):
            configured: list[Any] = [secret]
        elif isinstance(secret, (list, tuple)):
            configured = list(secret)
        else:
            configured = []
        secrets = [s for s in configured if isinstance(s, str) and s]
        if not secrets:
            return _fail("missing_secret")

        if not isinstance(signature_header, str) or not signature_header.strip():
            return _fail("missing_header")

        timestamp_text: str | None = None
        candidates: list[bytes] = []
        for part in signature_header.split(","):
            name, separator, value = part.strip().partition("=")
            if not separator:
                continue
            if name == "t" and timestamp_text is None:
                timestamp_text = value
            # Anything that is not exactly 64 lowercase hex characters is ignored, as are unknown
            # schemes.
            elif name == "v1" and _SIGNATURE_V1.fullmatch(value):
                candidates.append(value.encode("ascii"))
        if timestamp_text is None or not _TIMESTAMP.fullmatch(timestamp_text):
            return _fail("malformed_header")
        if not candidates:
            return _fail("malformed_header")

        now = time.time() if now_seconds is None else now_seconds
        tolerance = DEFAULT_TOLERANCE_SECONDS if tolerance_seconds is None else tolerance_seconds
        if abs(now - int(timestamp_text)) > tolerance:
            return _fail("timestamp_out_of_tolerance")

        body = _to_bytes(raw_body)
        matched = False
        # Every pair is compared in constant time and the loop never exits early.
        for one_secret in secrets:
            expected = compute_webhook_signature(one_secret, timestamp_text, body).encode("ascii")
            for candidate in candidates:
                if hmac.compare_digest(candidate, expected):
                    matched = True
        if matched:
            return SignatureVerificationResult(valid=True, reason="ok")
        return _fail("signature_mismatch")
    except Exception:
        return _fail("malformed_header")


class DeliveryDeduplicator(Protocol):
    def claim(self, delivery_id: str) -> bool:
        """Returns ``True`` the first time an id is seen and ``False`` for a duplicate."""
        ...

    def release(self, delivery_id: str) -> None:
        """Forgets an id so a later retry is processed again (call it when processing failed)."""
        ...


class InMemoryDeliveryDeduplicator:
    """In-memory deduplication for a single process (safe to share between threads).

    Retries of one delivery span up to 24 hours, so ids are kept for 48 hours by default. With several
    processes or instances, use a shared store (a unique key in your database is enough).
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = 48 * 60 * 60,
        max_entries: int = 50_000,
        now: Callable[[], float] = time.time,
    ) -> None:
        # A dict keeps insertion order, so the first entry is always the oldest one.
        self._seen: dict[str, float] = {}
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._now = now
        self._lock = threading.Lock()

    def claim(self, delivery_id: str) -> bool:
        with self._lock:
            now = self._now()
            for known_id, expires_at in list(self._seen.items()):
                if expires_at > now and len(self._seen) < self._max_entries:
                    break
                del self._seen[known_id]
            expires_at_known = self._seen.get(delivery_id)
            if expires_at_known is not None and expires_at_known > now:
                return False
            self._seen.pop(delivery_id, None)
            self._seen[delivery_id] = now + self._ttl_seconds
            return True

    def release(self, delivery_id: str) -> None:
        with self._lock:
            self._seen.pop(delivery_id, None)


WebhookHandlerReason = Literal[
    "ok",
    "missing_secret",
    "missing_header",
    "malformed_header",
    "timestamp_out_of_tolerance",
    "signature_mismatch",
    "invalid_payload",
    "delivery_id_mismatch",
    "duplicate",
    "handler_failed",
]


@dataclass(frozen=True)
class WebhookHandlerResult:
    #: HTTP status to answer with.
    status: int
    #: JSON body to answer with.
    body: dict[str, Any]
    #: Internal detail for your logs; never send it back to the caller.
    reason: WebhookHandlerReason


#: ``(raw_body, headers) -> WebhookHandlerResult``. Header names are matched case-insensitively.
WebhookHandler = Callable[[bytes, Mapping[str, str]], WebhookHandlerResult]


def _header(headers: Mapping[str, str], name: str) -> str | None:
    value = headers.get(name)
    if value is None:
        for key, candidate in headers.items():
            if isinstance(key, str) and key.lower() == name:
                value = candidate
                break
    return value if isinstance(value, str) else None


def create_webhook_handler(
    *,
    secret: str | Sequence[str],
    on_event: Callable[[WebhookEvent], None],
    deduplicator: DeliveryDeduplicator | None = None,
    tolerance_seconds: float | None = None,
    now_seconds: Callable[[], float] | None = None,
) -> WebhookHandler:
    """Framework-independent handling of one delivery: verify the signature over the raw body, parse,
    deduplicate and dispatch. Mount it on any HTTP server that can give you the raw bytes.

    :param secret: Signing secret (``whsec_...``), or current and previous during your rotation.
    :param on_event: Called once per verified, non-duplicate delivery. Keep it fast (GRUALINK waits 10
        seconds): store the event or enqueue it and do the heavy work later. If it raises, the answer
        is 500 and the delivery is retried.
    :param now_seconds: Clock in seconds, for tests.
    """
    dedup: DeliveryDeduplicator = deduplicator if deduplicator is not None else InMemoryDeliveryDeduplicator()

    def handle_webhook_delivery(raw_body: bytes, headers: Mapping[str, str]) -> WebhookHandlerResult:
        verification = verify_webhook_signature(
            raw_body=raw_body,
            signature_header=_header(headers, SIGNATURE_HEADER),
            secret=secret,
            now_seconds=now_seconds() if now_seconds is not None else None,
            tolerance_seconds=tolerance_seconds,
        )
        if not verification.valid:
            return WebhookHandlerResult(
                401, {"received": False, "error": "invalid_signature"}, verification.reason
            )

        try:
            # Only now, with the signature verified, is the body parsed.
            parsed = json.loads(bytes(raw_body).decode("utf-8"))
            if (
                not isinstance(parsed, dict)
                or not isinstance(parsed.get("id"), str)
                or not isinstance(parsed.get("type"), str)
            ):
                raise ValueError("not an event")
        except ValueError:
            return WebhookHandlerResult(
                400, {"received": False, "error": "invalid_payload"}, "invalid_payload"
            )
        event = cast(WebhookEvent, parsed)

        # The header is not covered by the signature; the `id` inside the signed body is. They must
        # agree.
        delivery_id = _header(headers, DELIVERY_HEADER)
        if delivery_id is None or delivery_id != event["id"]:
            return WebhookHandlerResult(
                400, {"received": False, "error": "delivery_id_mismatch"}, "delivery_id_mismatch"
            )

        if not dedup.claim(delivery_id):
            return WebhookHandlerResult(200, {"received": True, "duplicate": True}, "duplicate")
        try:
            on_event(event)
        except Exception:
            dedup.release(delivery_id)
            return WebhookHandlerResult(500, {"received": False, "error": "handler_failed"}, "handler_failed")
        return WebhookHandlerResult(200, {"received": True}, "ok")

    return handle_webhook_delivery

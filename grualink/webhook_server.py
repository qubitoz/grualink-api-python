"""A webhook receiver for the standard library ``http.server``. No framework needed."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from http.server import BaseHTTPRequestHandler
from io import BufferedIOBase
from typing import Any
from urllib.parse import urlsplit

from .types import WebhookEvent
from .webhooks import (
    MAX_WEBHOOK_BODY_BYTES,
    DeliveryDeduplicator,
    WebhookHandlerResult,
    create_webhook_handler,
)

# Unread bytes are discarded before refusing a request, up to this many, so that the caller can read
# the answer instead of seeing the connection reset.
_MAX_DISCARDED_BYTES = 1024 * 1024


class BodyTooLarge(Exception):
    """The request body is larger than the limit."""


def _read_exact(stream: BufferedIOBase, size: int) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise ValueError("the body ended early")
    return data


def _discard(stream: BufferedIOBase, size: int) -> None:
    remaining = min(size, _MAX_DISCARDED_BYTES)
    while remaining > 0:
        chunk = stream.read(min(remaining, 65536))
        if not chunk:
            return
        remaining -= len(chunk)


def read_raw_body(
    stream: BufferedIOBase, headers: Mapping[str, str], limit: int = MAX_WEBHOOK_BODY_BYTES
) -> bytes:
    """Reads the raw body with a hard cap, without ever buffering more than ``limit`` bytes.

    ``headers`` has lowercase names. Raises :class:`BodyTooLarge` beyond the limit and ``ValueError``
    when the body is not framed correctly.
    """
    if "chunked" in headers.get("transfer-encoding", "").lower():
        chunks: list[bytes] = []
        total = 0
        while True:
            size_line = stream.readline(1026)
            size = int(size_line.split(b";", 1)[0].strip(), 16)
            if size < 0:
                raise ValueError("invalid chunk size")
            if size == 0:
                # Trailers, then the blank line that ends the body.
                while stream.readline(1026) not in (b"\r\n", b"\n", b""):
                    pass
                return b"".join(chunks)
            total += size
            if total > limit:
                raise BodyTooLarge
            chunks.append(_read_exact(stream, size))
            stream.readline(4)

    declared = headers.get("content-length", "").strip()
    if not declared:
        return b""
    if not declared.isascii() or not declared.isdigit():
        raise ValueError("invalid Content-Length")
    length = int(declared)
    if length > limit:
        _discard(stream, length)
        raise BodyTooLarge
    return _read_exact(stream, length)


def create_webhook_listener(
    *,
    secret: str | Sequence[str],
    on_event: Callable[[WebhookEvent], None],
    deduplicator: DeliveryDeduplicator | None = None,
    tolerance_seconds: float | None = None,
    now_seconds: Callable[[], float] | None = None,
    path: str | None = None,
    on_result: Callable[[WebhookHandlerResult, Mapping[str, str]], None] | None = None,
) -> type[BaseHTTPRequestHandler]:
    """Returns a request handler class for ``http.server`` that receives GRUALINK webhooks.

    Takes the options of :func:`grualink.webhooks.create_webhook_handler`, plus:

    :param path: Only this path is served (default: any path).
    :param on_result: Called after each delivery with the outcome and the request headers (lowercase
        names), for your logs. The body and the secret are never passed.
    """
    handle = create_webhook_handler(
        secret=secret,
        on_event=on_event,
        deduplicator=deduplicator,
        tolerance_seconds=tolerance_seconds,
        now_seconds=now_seconds,
    )

    class WebhookRequestHandler(BaseHTTPRequestHandler):
        server_version = "webhook-receiver"
        sys_version = ""
        # A caller that stops sending does not hold a thread forever.
        timeout = 10
        _answered = False

        def _send(self, status: int, body: dict[str, Any], extra: Mapping[str, str] | None = None) -> None:
            text = json.dumps(body).encode("utf-8")
            self._answered = True
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(text)))
            self.send_header("Cache-Control", "no-store")
            for name, value in (extra or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(text)

        def _refuse(
            self,
            headers: Mapping[str, str],
            status: int,
            error: str,
            extra: Mapping[str, str] | None = None,
        ) -> None:
            self.close_connection = True
            declared = headers.get("content-length", "")
            if declared.isascii() and declared.isdigit():
                _discard(self.rfile, int(declared))
            self._send(status, {"received": False, "error": error}, extra)

        def _serve(self) -> None:
            self._answered = False
            headers: dict[str, str] = {}
            for name, value in self.headers.items():
                headers.setdefault(name.lower(), value)
            try:
                if path is not None and urlsplit(self.path).path != path:
                    self._refuse(headers, 404, "not_found")
                    return
                if self.command != "POST":
                    self._refuse(headers, 405, "method_not_allowed", {"Allow": "POST"})
                    return
                try:
                    raw_body = read_raw_body(self.rfile, headers)
                except BodyTooLarge:
                    self.close_connection = True
                    self._send(
                        413,
                        {"received": False, "error": "payload_too_large"},
                        {"Connection": "close"},
                    )
                    return
                except ValueError:
                    self.close_connection = True
                    self._send(400, {"received": False, "error": "invalid_request"})
                    return
                result = handle(raw_body, headers)
                self._send(result.status, result.body)
                if on_result is not None:
                    on_result(result, headers)
            except OSError:
                # The caller went away; there is nobody to answer.
                self.close_connection = True
            except Exception:
                self.close_connection = True
                if not self._answered:
                    self._send(500, {"received": False, "error": "internal_error"})

        do_GET = _serve
        do_HEAD = _serve
        do_POST = _serve
        do_PUT = _serve
        do_PATCH = _serve
        do_DELETE = _serve
        do_OPTIONS = _serve

        def log_message(self, format: str, *args: Any) -> None:
            # Quiet by default; use on_result for your own logs.
            return

    return WebhookRequestHandler

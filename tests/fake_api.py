"""A local stand-in for the API: records what it receives and answers what the test queued."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass
class RecordedRequest:
    method: str
    path: str
    #: Header names in lowercase.
    headers: dict[str, str]
    body: bytes


@dataclass
class FakeResponse:
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    #: Dictionaries and lists are sent as JSON; strings as they are.
    body: Any = None
    #: Wait before answering (to exercise the timeout).
    delay_seconds: float = 0.0
    #: Close the socket without answering (to exercise network errors).
    destroy: bool = False


Reply = FakeResponse | Callable[[RecordedRequest], FakeResponse]


class FakeApi:
    def __init__(self) -> None:
        self.requests: list[RecordedRequest] = []
        self._queue: list[Reply] = []
        self._lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                recorded = RecordedRequest(
                    method=self.command,
                    path=self.path,
                    headers={name.lower(): value for name, value in self.headers.items()},
                    body=self.rfile.read(length) if length else b"",
                )
                answer = fake._next(recorded)
                self.close_connection = True
                if answer.destroy:
                    self.connection.close()
                    return
                if answer.delay_seconds:
                    time.sleep(answer.delay_seconds)
                is_text = isinstance(answer.body, str)
                if answer.body is None:
                    payload = b""
                elif is_text:
                    payload = answer.body.encode("utf-8")
                else:
                    payload = json.dumps(answer.body).encode("utf-8")
                content_type = "text/html" if is_text else "application/json; charset=utf-8"
                try:
                    self.send_response(answer.status)
                    for name, value in {"Content-Type": content_type, **answer.headers}.items():
                        self.send_header(name, value)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except OSError:
                    # The client gave up (timeout tests).
                    pass

            do_GET = _serve
            do_POST = _serve

            def log_message(self, format: str, *args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}/api/v1"

    def _next(self, recorded: RecordedRequest) -> FakeResponse:
        with self._lock:
            self.requests.append(recorded)
            if not self._queue:
                return FakeResponse(status=500, body="no reply queued")
            reply = self._queue.pop(0) if len(self._queue) > 1 else self._queue[0]
        return reply(recorded) if callable(reply) else reply

    def reply(self, *responses: Reply) -> None:
        """Queue answers; the last one repeats once the queue is exhausted."""
        with self._lock:
            self._queue = list(responses)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def api_error(
    status: int,
    code: str,
    message: str = "Mensaje de ejemplo.",
    details: Any = None,
    headers: dict[str, str] | None = None,
) -> FakeResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details
    return FakeResponse(status=status, body={"error": error}, headers=headers or {})


SERVICE_REQUEST: dict[str, Any] = {
    "id": "11111111-2222-4333-8444-555555555555",
    "object": "service_request",
    "status": "quoted",
    "mode": "test",
    "external_ref": "TEST-CONFIRMAR-0001",
    "created_at": "2026-11-18T10:00:00.000Z",
    "resolved_at": None,
    "rejection_reason": None,
    "service": None,
    "quote": {
        "estimated_price": {
            "currency": "MXN",
            "subtotal": 864.33,
            "taxes": [{"name": "IVA", "type": "tax", "rate": 16, "amount": 138.29}],
            "tax_total": 138.29,
            "retention_total": 0,
            "total": 1002.62,
        },
        "includes_extras": False,
        "approximate_distance": True,
        "unpriced_reason": None,
        "note": "Precio estimado.",
    },
    "assignment": None,
    "timeline": None,
}

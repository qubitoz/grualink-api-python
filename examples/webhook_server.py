"""Example webhook receiver with the standard library http.server, no framework.

    GRUALINK_WEBHOOK_SECRET=<your whsec_ secret> python -m examples.webhook_server

Expose it over https (GRUALINK only delivers to https addresses on port 443) and give that address to
the towing company. Ask them to send a test event (webhook.test) to check the connection.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from http.server import ThreadingHTTPServer

from grualink import WebhookEvent, WebhookHandlerResult, create_webhook_listener

PATH = "/webhooks/grualink"

# Stand-in for your own queue or database. Answer fast and do the real work elsewhere.
queue: list[WebhookEvent] = []


def enqueue(event: WebhookEvent) -> None:
    queue.append(event)
    data = event.get("data") or {}
    sr = data.get("service_request")
    if sr:
        reference = sr.get("external_ref") or "no reference"
        print(
            f'[{event.get("mode")}] {event["type"]}: request {sr["id"]} ({reference}) is now "{sr["status"]}"'
        )
    else:
        print(f"[{event.get('mode')}] {event['type']}: {data.get('message', '')}")


def log_result(result: WebhookHandlerResult, headers: Mapping[str, str]) -> None:
    # The reason stays in your logs; the caller only sees a generic answer.
    if result.reason != "ok":
        delivery = headers.get("x-grualink-delivery", "-")
        print(f"Delivery {delivery!r}: {result.status} ({result.reason})", file=sys.stderr)


def main() -> int:
    secrets = [
        value
        for value in (
            os.environ.get("GRUALINK_WEBHOOK_SECRET"),
            os.environ.get("GRUALINK_WEBHOOK_SECRET_PREVIOUS"),
        )
        if value
    ]
    if not secrets:
        print(
            "Set GRUALINK_WEBHOOK_SECRET to your signing secret (it starts with whsec_). See .env.example.",
            file=sys.stderr,
        )
        return 1

    port_text = os.environ.get("PORT", "")
    port = int(port_text) if port_text.isascii() and port_text.isdigit() else 8787
    listener = create_webhook_listener(path=PATH, secret=secrets, on_event=enqueue, on_result=log_result)
    server = ThreadingHTTPServer(("127.0.0.1", port), listener)
    print(f"Listening for GRUALINK webhooks on http://localhost:{port}{PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

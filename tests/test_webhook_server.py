from __future__ import annotations

import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from typing import Any

from grualink import (
    InMemoryDeliveryDeduplicator,
    WebhookEvent,
    WebhookHandlerResult,
    build_signature_header,
    create_webhook_handler,
    create_webhook_listener,
)

SECRET = "whsec_TEST_ONLY_FAKE_SECRET_0001"
PREVIOUS = "whsec_TEST_ONLY_FAKE_SECRET_0000"
NOW = 1795000000
PATH = "/webhooks/grualink"


class Delivery:
    counter = 0

    def __init__(self, type: str = "service_request.confirmed", id: str | None = None) -> None:
        Delivery.counter += 1
        self.id = id or f"bbbbbbbb-0000-4000-8000-{Delivery.counter:012d}"
        self.type = type
        # Deliberately not what json.dumps would produce, and with non-ASCII text.
        self.body = (
            f'{{"id": "{self.id}", "data": {{"previous_status": "quoted", "service_request": '
            '{"id": "11111111-2222-4333-8444-555555555555", "status": "confirmed", '
            '"external_ref": "TEST-CONFIRMAR-ÑU"}}, "mode": "test", '
            f'"type": "{self.type}", "object": "event", "created_at": "2026-11-18T10:05:01.000Z"}}'
        ).encode()

    def headers(self, secrets: list[str] | None = None, t: int = NOW) -> dict[str, str]:
        return {
            "Content-Type": "application/json; charset=utf-8",
            "X-GruaLink-Event": self.type,
            "X-GruaLink-Delivery": self.id,
            "X-GruaLink-Timestamp": str(t),
            "X-GruaLink-Mode": "test",
            "X-GruaLink-Signature": build_signature_header(secrets or [SECRET], t, self.body),
        }


class Response:
    def __init__(self, raw: http.client.HTTPResponse) -> None:
        self.status = raw.status
        self.headers = {name.lower(): value for name, value in raw.getheaders()}
        self.json: Any = json.loads(raw.read())


class WebhookServerTests(unittest.TestCase):
    server: ThreadingHTTPServer
    thread: threading.Thread
    port: int
    received: list[WebhookEvent]
    results: list[WebhookHandlerResult]
    result_headers: list[dict[str, str]]
    fail_next = False
    clock = NOW

    @classmethod
    def setUpClass(cls) -> None:
        def on_event(event: WebhookEvent) -> None:
            if cls.fail_next:
                cls.fail_next = False
                raise RuntimeError("database is down")
            cls.received.append(event)

        def on_result(result: WebhookHandlerResult, headers: Any) -> None:
            cls.results.append(result)
            cls.result_headers.append(dict(headers))

        listener = create_webhook_listener(
            path=PATH,
            secret=[SECRET],
            now_seconds=lambda: cls.clock,
            on_event=on_event,
            on_result=on_result,
        )
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), listener)
        cls.server.daemon_threads = True
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self) -> None:
        cls = type(self)
        cls.received = []
        cls.results = []
        cls.result_headers = []
        cls.fail_next = False
        cls.clock = NOW

    def post(
        self,
        body: bytes,
        headers: dict[str, str],
        method: str = "POST",
        path: str = PATH,
        chunked: bool = False,
    ) -> Response:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            if chunked:
                pieces = iter([body[:7], body[7:40], body[40:]])
                connection.request(
                    method,
                    path,
                    body=pieces,
                    headers={**headers, "Transfer-Encoding": "chunked"},
                    encode_chunked=True,
                )
            else:
                connection.request(method, path, body=body if method == "POST" else None, headers=headers)
            return Response(connection.getresponse())
        finally:
            connection.close()

    def test_a_signed_delivery_is_accepted_and_handed_to_on_event(self) -> None:
        d = Delivery()
        response = self.post(d.body, d.headers())
        self.assertEqual(response.status, 200)
        self.assertEqual(response.json, {"received": True})
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(len(self.received), 1)
        self.assertEqual(self.received[0]["id"], d.id)
        self.assertEqual(self.received[0]["data"]["service_request"]["external_ref"], "TEST-CONFIRMAR-ÑU")
        self.assertEqual(self.results[0].reason, "ok")
        self.assertEqual(self.result_headers[0]["x-grualink-delivery"], d.id)

    def test_a_delivery_sent_in_chunks_is_read_whole(self) -> None:
        d = Delivery()
        response = self.post(d.body, d.headers(), chunked=True)
        self.assertEqual(response.status, 200)
        self.assertEqual(self.received[0]["id"], d.id)

    def test_a_retry_of_the_same_delivery_answers_200_but_is_processed_once(self) -> None:
        d = Delivery()
        self.assertEqual(self.post(d.body, d.headers()).status, 200)
        type(self).clock = NOW + 60
        again = self.post(d.body, d.headers(t=NOW + 60))
        self.assertEqual(again.status, 200)
        self.assertEqual(again.json, {"received": True, "duplicate": True})
        self.assertEqual(len(self.received), 1)
        self.assertEqual([r.reason for r in self.results], ["ok", "duplicate"])

    def test_when_on_event_fails_the_answer_is_500_and_the_retry_is_processed(self) -> None:
        d = Delivery()
        type(self).fail_next = True
        first = self.post(d.body, d.headers())
        self.assertEqual(first.status, 500)
        self.assertEqual(first.json, {"received": False, "error": "handler_failed"})
        self.assertEqual(len(self.received), 0)
        retry = self.post(d.body, d.headers())
        self.assertEqual(retry.status, 200)
        self.assertEqual(retry.json, {"received": True})
        self.assertEqual(len(self.received), 1)

    def test_during_a_rotation_a_delivery_signed_with_both_secrets_is_accepted(self) -> None:
        d = Delivery()
        both = d.headers(["whsec_TEST_ONLY_FAKE_SECRET_NEXT", SECRET])
        self.assertEqual(self.post(d.body, both).status, 200)
        old = Delivery()
        self.assertEqual(self.post(old.body, old.headers([PREVIOUS])).status, 401)

    def test_a_tampered_body_a_missing_signature_and_a_stale_timestamp_get_401(self) -> None:
        d = Delivery()
        tampered = self.post(d.body.replace(b"confirmed", b"completed"), d.headers())
        self.assertEqual(tampered.status, 401)
        # No detail about why.
        self.assertEqual(tampered.json, {"received": False, "error": "invalid_signature"})

        unsigned = {k: v for k, v in d.headers().items() if k != "X-GruaLink-Signature"}
        missing = self.post(d.body, unsigned)
        self.assertEqual(missing.status, 401)
        self.assertEqual(missing.json, tampered.json)

        type(self).clock = NOW + 301
        stale = self.post(d.body, d.headers())
        self.assertEqual(stale.status, 401)
        self.assertEqual(stale.json, tampered.json)

        self.assertEqual(
            [r.reason for r in self.results],
            ["signature_mismatch", "missing_header", "timestamp_out_of_tolerance"],
        )
        self.assertEqual(len(self.received), 0)

    def test_the_body_parsed_and_serialized_again_does_not_verify(self) -> None:
        d = Delivery()
        reserialized = json.dumps(json.loads(d.body)).encode()
        self.assertNotEqual(reserialized, d.body)
        self.assertEqual(self.post(reserialized, d.headers()).status, 401)

    def test_a_delivery_header_that_disagrees_with_the_signed_id_is_rejected(self) -> None:
        d = Delivery()
        headers = {**d.headers(), "X-GruaLink-Delivery": "cccccccc-0000-4000-8000-000000000000"}
        response = self.post(d.body, headers)
        self.assertEqual(response.status, 400)
        self.assertEqual(self.results[0].reason, "delivery_id_mismatch")
        self.assertEqual(len(self.received), 0)

        missing = {k: v for k, v in d.headers().items() if k != "X-GruaLink-Delivery"}
        self.assertEqual(self.post(d.body, missing).status, 400)
        self.assertEqual(len(self.received), 0)

    def test_a_signed_body_that_is_not_an_event_is_a_400(self) -> None:
        for body in (b"[1, 2, 3]", b'{"id": 7, "type": "webhook.test"}', b"\xff\xfe", b"not json"):
            with self.subTest(body=body):
                type(self).results = []
                d = Delivery(type="webhook.test", id="x")
                d.body = body
                response = self.post(body, d.headers())
                self.assertEqual(response.status, 400)
                self.assertEqual(self.results[0].reason, "invalid_payload")
        self.assertEqual(len(self.received), 0)

    def test_other_methods_other_paths_and_oversized_bodies_are_refused(self) -> None:
        get = self.post(b"", {}, method="GET")
        self.assertEqual(get.status, 405)
        self.assertEqual(get.headers["allow"], "POST")
        self.assertEqual(self.post(b"{}", {}, path="/other").status, 404)
        # The path is checked before the method, and a query string is not part of the path.
        self.assertEqual(self.post(b"", {}, method="GET", path="/other").status, 404)
        d = Delivery()
        self.assertEqual(self.post(d.body, d.headers(), path=f"{PATH}?source=test").status, 200)

        big = b" " * (64 * 1024 + 1)
        self.assertEqual(self.post(big, {"Content-Type": "application/json"}).status, 413)
        self.assertEqual(self.post(big, {"Content-Type": "application/json"}, chunked=True).status, 413)
        at_the_limit = self.post(b" " * (64 * 1024), {"Content-Type": "application/json"})
        self.assertEqual(at_the_limit.status, 401)
        self.assertEqual(len(self.received), 1)


class HandlerTests(unittest.TestCase):
    def test_header_names_are_matched_whatever_their_case(self) -> None:
        received: list[WebhookEvent] = []
        handle = create_webhook_handler(secret=SECRET, on_event=received.append, now_seconds=lambda: NOW)
        d = Delivery()
        for headers in (
            d.headers(),
            {k.lower(): v for k, v in d.headers().items()},
            {k.upper(): v for k, v in d.headers().items()},
        ):
            result = handle(d.body, headers)
            self.assertEqual(result.status, 200)
        self.assertEqual(len(received), 1)

    def test_a_custom_deduplicator_is_used(self) -> None:
        class Recorder:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            def claim(self, delivery_id: str) -> bool:
                self.calls.append(("claim", delivery_id))
                return True

            def release(self, delivery_id: str) -> None:
                self.calls.append(("release", delivery_id))

        def fail(event: WebhookEvent) -> None:
            raise RuntimeError("queue is full")

        recorder = Recorder()
        handle = create_webhook_handler(
            secret=SECRET, on_event=fail, deduplicator=recorder, now_seconds=lambda: NOW
        )
        d = Delivery()
        self.assertEqual(handle(d.body, d.headers()).status, 500)
        self.assertEqual(recorder.calls, [("claim", d.id), ("release", d.id)])

    def test_an_unverified_delivery_never_reaches_the_deduplicator(self) -> None:
        dedup = InMemoryDeliveryDeduplicator()
        handle = create_webhook_handler(
            secret=SECRET, on_event=lambda event: None, deduplicator=dedup, now_seconds=lambda: NOW
        )
        d = Delivery()
        self.assertEqual(handle(d.body, d.headers([PREVIOUS])).status, 401)
        # The id was not burned by the forged delivery: the real one is still processed.
        self.assertEqual(handle(d.body, d.headers()).body, {"received": True})


class DeduplicatorTests(unittest.TestCase):
    def test_claim_release_expiry_and_size_cap(self) -> None:
        now = 0.0
        dedup = InMemoryDeliveryDeduplicator(ttl_seconds=1, max_entries=3, now=lambda: now)
        self.assertIs(dedup.claim("a"), True)
        self.assertIs(dedup.claim("a"), False)
        dedup.release("a")
        self.assertIs(dedup.claim("a"), True)

        now = 1.001
        self.assertIs(dedup.claim("a"), True, "an expired id can be claimed again")

        self.assertIs(dedup.claim("b"), True)
        self.assertIs(dedup.claim("c"), True)
        self.assertIs(dedup.claim("d"), True, "the oldest entry is evicted at the cap")
        self.assertIs(dedup.claim("d"), False)
        self.assertIs(dedup.claim("a"), True, "the evicted id is no longer remembered")

    def test_concurrent_claims_of_one_id_have_a_single_winner(self) -> None:
        dedup = InMemoryDeliveryDeduplicator()
        wins: list[bool] = []
        barrier = threading.Barrier(16)

        def worker() -> None:
            barrier.wait()
            wins.append(dedup.claim("same-id"))

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(wins.count(True), 1)


if __name__ == "__main__":
    unittest.main()

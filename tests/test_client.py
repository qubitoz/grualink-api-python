from __future__ import annotations

import contextlib
import io
import json
import logging
import pprint
import re
import traceback
import unittest
from collections.abc import Callable, Mapping
from typing import Any

from grualink import (
    GrualinkApiError,
    GrualinkAuthenticationError,
    GrualinkClient,
    GrualinkConfigError,
    GrualinkConnectionError,
    GrualinkError,
    GrualinkIdempotencyConflictError,
    GrualinkInvalidRequestError,
    GrualinkNotFoundError,
    GrualinkPermissionError,
    GrualinkRateLimitError,
    GrualinkServerError,
    GrualinkTimeoutError,
    GrualinkValidationError,
    RetryInfo,
    ServiceRequestInput,
    TransportResponse,
)

from .fake_api import SERVICE_REQUEST, FakeApi, FakeResponse, RecordedRequest, api_error

# Obviously fake: it only has the prefix the client checks.
TEST_KEY = "glk_test_FAKE_KEY_FOR_UNIT_TESTS_ONLY"

INPUT: ServiceRequestInput = {
    "external_ref": "TEST-CONFIRMAR-0001",
    "contact": {"name": "Persona de Ejemplo", "phone": "+525500000000"},
    "pickup": {"address": "Calle Ficticia 123, Ciudad Ejemplo", "lat": 20.6767, "lng": -103.3475},
    "destination": {
        "address": "Taller Ficticio, Avenida Ejemplo 456",
        "lat": 20.7214,
        "lng": -103.3918,
    },
}

ERROR_TABLE: list[tuple[int, str, type[GrualinkApiError]]] = [
    (400, "invalid_request", GrualinkInvalidRequestError),
    (400, "invalid_json", GrualinkInvalidRequestError),
    (400, "key_in_query", GrualinkInvalidRequestError),
    (400, "idempotency_key_required", GrualinkInvalidRequestError),
    (400, "invalid_idempotency_key", GrualinkInvalidRequestError),
    (401, "unauthorized", GrualinkAuthenticationError),
    (403, "insufficient_scope", GrualinkPermissionError),
    (403, "customer_unavailable", GrualinkPermissionError),
    (403, "live_mode_unavailable", GrualinkPermissionError),
    (403, "browser_not_allowed", GrualinkPermissionError),
    (404, "not_found", GrualinkNotFoundError),
    (405, "method_not_allowed", GrualinkInvalidRequestError),
    (409, "idempotency_conflict", GrualinkIdempotencyConflictError),
    (413, "payload_too_large", GrualinkInvalidRequestError),
    (415, "unsupported_media_type", GrualinkInvalidRequestError),
    (422, "validation_error", GrualinkValidationError),
    (429, "too_many_open_requests", GrualinkRateLimitError),
]


class ClientTestCase(unittest.TestCase):
    api: FakeApi

    @classmethod
    def setUpClass(cls) -> None:
        cls.api = FakeApi()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.api.close()

    def setUp(self) -> None:
        self.api.requests.clear()
        self.sleeps: list[float] = []
        self.retries: list[RetryInfo] = []

    def client(self, **overrides: Any) -> GrualinkClient:
        options: dict[str, Any] = {
            "base_url": self.api.base_url,
            "sleep": self.sleeps.append,
            "random": lambda: 0.5,
            "on_retry": self.retries.append,
        }
        options.update(overrides)
        return GrualinkClient(TEST_KEY, **options)

    def rejection(self, call: Callable[[], Any]) -> GrualinkError:
        with self.assertRaises(GrualinkError) as caught:
            call()
        return caught.exception


class SuccessTests(ClientTestCase):
    def test_create_sends_the_key_the_idempotency_key_and_the_json_body(self) -> None:
        self.api.reply(FakeResponse(status=201, body=SERVICE_REQUEST))
        result = self.client().create_service_request(INPUT, idempotency_key="order-0001")

        self.assertEqual(result.service_request["id"], SERVICE_REQUEST["id"])
        price = result.service_request["quote"]["estimated_price"]
        assert price is not None
        self.assertEqual(price["total"], 1002.62)
        self.assertIs(result.replayed, False)
        self.assertEqual(result.idempotency_key, "order-0001")

        self.assertEqual(len(self.api.requests), 1)
        sent = self.api.requests[0]
        self.assertEqual(sent.method, "POST")
        self.assertEqual(sent.path, "/api/v1/service-requests")
        self.assertEqual(sent.headers["authorization"], f"Bearer {TEST_KEY}")
        self.assertEqual(sent.headers["idempotency-key"], "order-0001")
        self.assertEqual(sent.headers["content-type"], "application/json")
        self.assertEqual(json.loads(sent.body), INPUT)

    def test_create_generates_an_idempotency_key_when_none_is_given(self) -> None:
        self.api.reply(FakeResponse(status=201, body=SERVICE_REQUEST))
        result = self.client().create_service_request(INPUT)
        self.assertRegex(
            result.idempotency_key,
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        )
        self.assertEqual(self.api.requests[0].headers["idempotency-key"], result.idempotency_key)

    def test_a_repeated_request_is_a_200_with_the_replay_header_and_is_reported(self) -> None:
        self.api.reply(
            FakeResponse(status=201, body=SERVICE_REQUEST),
            FakeResponse(status=200, body=SERVICE_REQUEST, headers={"Idempotent-Replayed": "true"}),
        )
        client = self.client()
        first = client.create_service_request(INPUT, idempotency_key="order-0002")
        second = client.create_service_request(INPUT, idempotency_key="order-0002")

        self.assertIs(first.replayed, False)
        self.assertIs(second.replayed, True)
        self.assertEqual(second.service_request, first.service_request)
        # The same bytes are sent both times.
        self.assertEqual(self.api.requests[0].body, self.api.requests[1].body)

    def test_a_200_without_the_replay_header_is_not_reported_as_replayed(self) -> None:
        self.api.reply(FakeResponse(status=200, body=SERVICE_REQUEST))
        self.assertIs(self.client().create_service_request(INPUT).replayed, False)

    def test_non_ascii_text_is_sent_as_utf8(self) -> None:
        self.api.reply(FakeResponse(status=201, body=SERVICE_REQUEST))
        data: ServiceRequestInput = {**INPUT, "notes": "Señal débil: «llamar» ☎"}
        self.client().create_service_request(data)
        body = self.api.requests[0].body
        self.assertIn("Señal débil: «llamar» ☎".encode(), body)
        self.assertEqual(json.loads(body.decode("utf-8")), data)

    def test_get_and_find_by_external_ref(self) -> None:
        self.api.reply(FakeResponse(status=200, body=SERVICE_REQUEST))
        one = self.client().get_service_request(SERVICE_REQUEST["id"])
        self.assertEqual(one["status"], "quoted")
        self.assertEqual(self.api.requests[0].method, "GET")
        self.assertEqual(self.api.requests[0].path, f"/api/v1/service-requests/{SERVICE_REQUEST['id']}")

        self.api.reply(FakeResponse(status=200, body={"object": "list", "data": [SERVICE_REQUEST]}))
        found = self.client().find_service_requests_by_external_ref("SIN 2026/001 ñ")
        self.assertEqual(len(found), 1)
        self.assertEqual(
            self.api.requests[1].path,
            "/api/v1/service-requests?external_ref=SIN+2026%2F001+%C3%B1",
        )

    def test_health_is_sent_without_the_key(self) -> None:
        self.api.reply(FakeResponse(status=200, body={"status": "ok", "version": "v1"}))
        self.assertEqual(self.client().health(), {"status": "ok", "version": "v1"})
        self.assertEqual(self.api.requests[0].path, "/api/v1/health")
        self.assertNotIn("authorization", self.api.requests[0].headers)


class ErrorTests(ClientTestCase):
    def test_every_documented_error_has_its_class_and_is_not_retried(self) -> None:
        self.assertEqual(len(ERROR_TABLE), 17)
        for status, code, error_class in ERROR_TABLE:
            with self.subTest(status=status, code=code):
                self.api.requests.clear()
                self.sleeps.clear()
                self.api.reply(api_error(status, code, "Mensaje de ejemplo."))
                error = self.rejection(lambda: self.client().create_service_request(INPUT))
                self.assertIs(type(error), error_class)
                assert isinstance(error, GrualinkApiError)
                self.assertEqual(error.status, status)
                self.assertEqual(error.code, code)
                self.assertEqual(error.message, "Mensaje de ejemplo.")
                self.assertEqual(str(error), "Mensaje de ejemplo.")
                self.assertEqual(error.attempts, 1)
                self.assertEqual(len(self.api.requests), 1)
                self.assertEqual(self.sleeps, [])

    def test_422_exposes_the_invalid_fields_in_details(self) -> None:
        details = [
            {"field": "contact.phone", "issue": "invalid_format"},
            {"field": "pickup", "issue": "invalid_value"},
        ]
        self.api.reply(api_error(422, "validation_error", "Hay campos inválidos.", details))
        error = self.rejection(lambda: self.client().create_service_request(INPUT))
        self.assertIsInstance(error, GrualinkValidationError)
        assert isinstance(error, GrualinkValidationError)
        self.assertEqual(error.details, details)

    def test_an_error_body_that_is_not_json_is_unexpected_response_and_is_not_echoed(self) -> None:
        self.api.reply(FakeResponse(status=404, body="<html>proxy page</html>"))
        error = self.rejection(lambda: self.client().get_service_request("abc"))
        self.assertIsInstance(error, GrualinkNotFoundError)
        assert isinstance(error, GrualinkApiError)
        self.assertEqual(error.code, "unexpected_response")
        self.assertNotIn("proxy page", error.message)

    def test_a_success_body_that_is_not_json_is_unexpected_response(self) -> None:
        self.api.reply(FakeResponse(status=200, body="<html>ok</html>"))
        error = self.rejection(lambda: self.client().get_service_request("abc"))
        self.assertIs(type(error), GrualinkApiError)
        assert isinstance(error, GrualinkApiError)
        self.assertEqual(error.code, "unexpected_response")
        self.assertEqual(len(self.api.requests), 1)

    def test_a_redirect_is_not_followed(self) -> None:
        self.api.reply(FakeResponse(status=302, headers={"Location": f"{self.api.base_url}/elsewhere"}))
        error = self.rejection(lambda: self.client().get_service_request(SERVICE_REQUEST["id"]))
        self.assertIsInstance(error, GrualinkApiError)
        assert isinstance(error, GrualinkApiError)
        self.assertEqual(error.status, 302)
        # The key did not travel to the new address: nothing else was requested.
        self.assertEqual(len(self.api.requests), 1)


class RetryTests(ClientTestCase):
    def test_429_rate_limited_waits_at_least_retry_after_and_resends_key_and_body(self) -> None:
        self.api.reply(
            api_error(429, "rate_limited", "Demasiadas peticiones.", None, {"Retry-After": "7"}),
            FakeResponse(status=201, body=SERVICE_REQUEST),
        )
        result = self.client().create_service_request(INPUT, idempotency_key="order-0003")
        self.assertEqual(result.service_request["id"], SERVICE_REQUEST["id"])
        self.assertEqual(len(self.api.requests), 2)
        # 7 s from the header plus half (random = 0.5) of the first backoff step (0.5 s).
        self.assertEqual(self.sleeps, [7.25])
        self.assertEqual(self.retries[0].attempt, 1)
        self.assertEqual(self.retries[0].delay_seconds, 7.25)
        self.assertIsInstance(self.retries[0].error, GrualinkRateLimitError)
        first, second = self.api.requests
        self.assertEqual(first.headers["idempotency-key"], "order-0003")
        self.assertEqual(second.headers["idempotency-key"], "order-0003")
        self.assertEqual(first.body, second.body)

    def test_retry_after_is_capped_and_accepts_an_http_date(self) -> None:
        self.api.reply(
            api_error(429, "rate_limited", headers={"Retry-After": "3600"}),
            api_error(429, "rate_limited", headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}),
            api_error(429, "rate_limited", headers={"Retry-After": "soon"}),
            FakeResponse(status=200, body=SERVICE_REQUEST),
        )
        self.client().get_service_request(SERVICE_REQUEST["id"])
        # Capped at 60 s; a date in the past is zero; an unreadable value is ignored.
        self.assertEqual(self.sleeps, [60.25, 0.5, 1.0])

    def test_5xx_is_retried_with_exponential_backoff_and_jitter_then_succeeds(self) -> None:
        self.api.reply(
            api_error(500, "internal_error"),
            FakeResponse(status=502, body="<html>bad gateway</html>"),
            api_error(503, "internal_error"),
            FakeResponse(status=201, body=SERVICE_REQUEST),
        )
        result = self.client().create_service_request(INPUT)
        self.assertEqual(result.service_request["id"], SERVICE_REQUEST["id"])
        self.assertEqual(len(self.api.requests), 4)
        # random = 0.5 over ceilings of 0.5, 1 and 2 s.
        self.assertEqual(self.sleeps, [0.25, 0.5, 1.0])
        self.assertEqual(len({r.headers["idempotency-key"] for r in self.api.requests}), 1)
        self.assertEqual(len({r.body for r in self.api.requests}), 1)

    def test_the_backoff_is_capped_and_the_jitter_comes_from_the_injected_source(self) -> None:
        self.api.reply(api_error(500, "internal_error"))
        client = self.client(max_retries=6, random=lambda: 1.0)
        error = self.rejection(lambda: client.get_service_request(SERVICE_REQUEST["id"]))
        self.assertIsInstance(error, GrualinkServerError)
        self.assertEqual(self.sleeps, [0.5, 1.0, 2.0, 4.0, 8.0, 8.0])

    def test_it_gives_up_after_max_retries_and_reports_the_attempts(self) -> None:
        self.api.reply(api_error(500, "internal_error", "Error interno."))
        client = self.client(max_retries=2)
        error = self.rejection(lambda: client.create_service_request(INPUT))
        self.assertIsInstance(error, GrualinkServerError)
        assert isinstance(error, GrualinkServerError)
        self.assertEqual(error.code, "internal_error")
        self.assertEqual(error.attempts, 3)
        self.assertEqual(len(self.api.requests), 3)
        self.assertEqual(len(self.sleeps), 2)

    def test_max_retries_zero_disables_retries(self) -> None:
        self.api.reply(api_error(500, "internal_error"))
        client = self.client(max_retries=0)
        error = self.rejection(lambda: client.get_service_request(SERVICE_REQUEST["id"]))
        self.assertEqual(error.attempts, 1)
        self.assertEqual(len(self.api.requests), 1)

    def test_a_network_error_is_retried_and_surfaces_as_connection_error(self) -> None:
        self.api.reply(FakeResponse(destroy=True), FakeResponse(status=200, body=SERVICE_REQUEST))
        found = self.client().get_service_request(SERVICE_REQUEST["id"])
        self.assertEqual(found["id"], SERVICE_REQUEST["id"])
        self.assertIsInstance(self.retries[0].error, GrualinkConnectionError)

        self.api.reply(FakeResponse(destroy=True))
        client = self.client(max_retries=1)
        error = self.rejection(lambda: client.get_service_request(SERVICE_REQUEST["id"]))
        self.assertIsInstance(error, GrualinkConnectionError)
        self.assertEqual(error.attempts, 2)

    def test_a_slow_answer_hits_the_timeout_is_retried_and_surfaces_as_timeout(self) -> None:
        self.api.reply(FakeResponse(status=200, body=SERVICE_REQUEST, delay_seconds=0.4))
        client = self.client(timeout_seconds=0.05, max_retries=1)
        error = self.rejection(lambda: client.get_service_request(SERVICE_REQUEST["id"]))
        self.assertIsInstance(error, GrualinkTimeoutError)
        self.assertEqual(error.attempts, 2)
        self.assertEqual(len(self.api.requests), 2)


class LocalValidationTests(ClientTestCase):
    def test_invalid_configuration_and_arguments_fail_before_any_request(self) -> None:
        bad_options: list[tuple[str, dict[str, Any]]] = [
            ("", {}),
            ("not-a-key", {}),
            ("glk_test_", {}),
            (TEST_KEY, {"base_url": "http://example.com/api/v1"}),
            (TEST_KEY, {"base_url": "example.com/api/v1"}),
            (TEST_KEY, {"base_url": f"https://example.com/api/v1?key={TEST_KEY}"}),
            (TEST_KEY, {"base_url": "https://user:secret@example.com/api/v1"}),
            (TEST_KEY, {"timeout_seconds": 0}),
            (TEST_KEY, {"max_retries": -1}),
            (TEST_KEY, {"max_retries": 1.5}),
        ]
        for api_key, options in bad_options:
            with self.subTest(options=sorted(options)), self.assertRaises(GrualinkConfigError):
                GrualinkClient(api_key, **options)

        client = self.client()
        for idempotency_key in ["", "with space", "ñ", "x" * 256, "line\nbreak"]:
            with self.subTest(key=idempotency_key[:12]), self.assertRaises(GrualinkConfigError):
                client.create_service_request(INPUT, idempotency_key=idempotency_key)
        too_large: ServiceRequestInput = {**INPUT, "notes": "x" * (17 * 1024)}
        calls: list[Callable[[], Any]] = [
            lambda: client.create_service_request(too_large),
            lambda: client.create_service_request([INPUT]),  # type: ignore[arg-type]
            lambda: client.create_service_request({**INPUT, "notes": float("nan")}),  # type: ignore[typeddict-item]
            lambda: client.get_service_request(" "),
            lambda: client.find_service_requests_by_external_ref(""),
        ]
        for index, call in enumerate(calls):
            with self.subTest(call=index), self.assertRaises(GrualinkConfigError) as caught:
                call()
            self.assertEqual(caught.exception.attempts, 0)
        self.assertEqual(len(self.api.requests), 0)

    def test_a_bad_key_is_never_echoed_by_the_configuration_error(self) -> None:
        almost = "glk_prod_FAKE_BUT_SHOULD_NOT_BE_ECHOED"
        with self.assertRaises(GrualinkConfigError) as caught:
            GrualinkClient(almost)
        self.assertNotIn(almost, "".join(traceback.format_exception(caught.exception)))
        self.assertNotIn(almost, repr(caught.exception))

    def test_the_mode_comes_from_the_key_prefix(self) -> None:
        self.assertEqual(self.client().mode, "test")
        self.assertEqual(GrualinkClient("glk_live_FAKE_KEY_FOR_UNIT_TESTS_ONLY").mode, "live")


def describe(error: BaseException) -> str:
    """Everything a program could print about an error, local variables of every frame included."""
    detailed = traceback.TracebackException.from_exception(error, capture_locals=True)
    return "\n".join(
        [
            str(error),
            repr(error),
            repr(error.args),
            pprint.pformat(vars(error)),
            json.dumps(vars(error), default=repr),
            "".join(traceback.format_exception(error)),
            "".join(detailed.format()),
            repr(error.__cause__),
            repr(error.__context__),
        ]
    )


class KeyNeverLeaksTests(ClientTestCase):
    def test_the_api_key_never_appears_in_errors_logs_or_the_client_itself(self) -> None:
        output = io.StringIO()
        log_handler = logging.StreamHandler(output)
        root_logger = logging.getLogger()
        previous_level = root_logger.level
        root_logger.addHandler(log_handler)
        root_logger.setLevel(logging.DEBUG)
        errors: list[GrualinkError] = []

        def log_retry(info: RetryInfo) -> None:
            logging.getLogger("example").warning("retrying %r %s", info, describe(info.error))
            print("retrying", info)

        # A server that (wrongly) echoes the Authorization header back in every field of the error.
        def echo(status: int) -> Callable[[RecordedRequest], FakeResponse]:
            def reply(request: RecordedRequest) -> FakeResponse:
                sent = request.headers.get("authorization")
                return api_error(
                    status,
                    f"code {sent}",
                    f"message {sent}",
                    [{"field": f"field {sent}", "issue": f"issue {sent}"}],
                )

            return reply

        # A transport that raises an error mentioning the key.
        def leaky_transport(
            method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
        ) -> TransportResponse:
            raise OSError(f"connect failed for {headers.get('Authorization')}")

        try:
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                client = self.client(max_retries=1, on_retry=log_retry)
                print(client, repr(client), str(vars(client)))
                pprint.pprint(vars(client))

                for status in [400, 401, 403, 404, 409, 422, 429, 500]:
                    self.api.reply(echo(status))
                    errors.append(self.rejection(lambda: client.create_service_request(INPUT)))
                self.api.reply(FakeResponse(destroy=True))
                errors.append(self.rejection(lambda: client.get_service_request(SERVICE_REQUEST["id"])))
                self.api.reply(FakeResponse(status=200, body=SERVICE_REQUEST, delay_seconds=0.3))
                slow = self.client(timeout_seconds=0.03, max_retries=0)
                errors.append(self.rejection(lambda: slow.get_service_request(SERVICE_REQUEST["id"])))
                leaky = self.client(transport=leaky_transport, max_retries=0)
                errors.append(self.rejection(lambda: leaky.get_service_request(SERVICE_REQUEST["id"])))

                for error in errors:
                    print(describe(error))
                    logging.getLogger("example").error("failed: %s", error, exc_info=error)
        finally:
            root_logger.removeHandler(log_handler)
            root_logger.setLevel(previous_level)

        self.assertEqual(len(errors), 11)
        self.assertTrue(
            any(r.headers.get("authorization") == f"Bearer {TEST_KEY}" for r in self.api.requests),
            "the key was really sent",
        )
        everything = "\n".join(
            [
                output.getvalue(),
                repr(client),
                str(client),
                pprint.pformat(vars(client)),
                json.dumps(vars(client), default=repr),
                *(describe(error) for error in errors),
            ]
        )
        self.assertGreater(len(output.getvalue()), 2000, "the output was really captured")
        self.assertIn("retrying", everything)
        self.assertNotIn(TEST_KEY, everything, "the API key leaked")
        self.assertNotIn(TEST_KEY[9:], everything, "part of the API key leaked")
        self.assertIn("[REDACTED]", everything, "echoed keys are replaced by a marker")
        for error in errors:
            self.assertIsNone(error.__cause__)
            self.assertIsNone(error.__context__)

    def test_the_key_cannot_be_serialized_with_the_client(self) -> None:
        import copy
        import pickle

        client = self.client()
        with self.assertRaises(TypeError):
            pickle.dumps(client)
        with self.assertRaises(TypeError):
            copy.deepcopy(client)

    def test_the_fake_key_is_obviously_fake(self) -> None:
        self.assertTrue(re.fullmatch(r"glk_test_FAKE_[A-Z_]+", TEST_KEY))


if __name__ == "__main__":
    unittest.main()

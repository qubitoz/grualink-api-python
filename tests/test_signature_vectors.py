from __future__ import annotations

import base64
import json
import unittest
from pathlib import Path
from typing import Any

from grualink import (
    SignatureVerificationResult,
    build_signature_header,
    compute_webhook_signature,
    verify_webhook_signature,
)

ROOT = Path(__file__).resolve().parent.parent
VECTORS: dict[str, Any] = json.loads(
    (ROOT / "contract" / "signature-vectors.json").read_text(encoding="utf-8")
)
CASES: list[dict[str, Any]] = VECTORS["cases"]


def case_named(name: str) -> dict[str, Any]:
    return next(c for c in CASES if c["name"] == name)


class VectorFileTests(unittest.TestCase):
    def test_the_vector_file_has_the_expected_shape(self) -> None:
        self.assertEqual(VECTORS["version"], 1)
        self.assertEqual(VECTORS["tolerance_seconds"], 300)
        self.assertGreaterEqual(len(CASES), 30)
        self.assertEqual(len({c["name"] for c in CASES}), len(CASES), "case names are unique")
        self.assertTrue(any(c["expected"]["valid"] for c in CASES))
        self.assertTrue(any(not c["expected"]["valid"] for c in CASES))
        for reason in VECTORS["reasons"]:
            self.assertTrue(
                any(c["expected"]["reason"] == reason for c in CASES),
                f"no case covers the reason {reason}",
            )

    def test_no_case_is_skipped(self) -> None:
        generated = {name for name in dir(SignatureVectorTests) if name.startswith("test_vector_")}
        self.assertEqual(generated, {f"test_vector_{c['name']}" for c in CASES})
        result = unittest.TestResult()
        unittest.defaultTestLoader.loadTestsFromTestCase(SignatureVectorTests).run(result)
        self.assertEqual(result.testsRun, len(CASES))
        self.assertEqual(result.skipped, [])
        self.assertTrue(result.wasSuccessful(), result.failures + result.errors)


class SignatureVectorTests(unittest.TestCase):
    """One test per case of contract/signature-vectors.json (added below)."""

    def check(self, case: dict[str, Any]) -> None:
        raw_body = base64.b64decode(case["body_base64"], validate=True)
        if case["body"] is not None:
            self.assertEqual(case["body"].encode("utf-8"), raw_body, "body and body_base64 agree")
        result = verify_webhook_signature(
            raw_body=raw_body,
            signature_header=case["headers"].get("x-grualink-signature"),
            secret=case["secret"],
            now_seconds=case["now"],
            tolerance_seconds=VECTORS["tolerance_seconds"],
        )
        expected = SignatureVerificationResult(
            valid=case["expected"]["valid"], reason=case["expected"]["reason"]
        )
        self.assertEqual(result, expected, case["description"])


def _add_vector_test(case: dict[str, Any]) -> None:
    def test(self: SignatureVectorTests) -> None:
        self.check(case)

    test.__doc__ = case["description"]
    setattr(SignatureVectorTests, f"test_vector_{case['name']}", test)


for _case in CASES:
    _add_vector_test(_case)


class VerificationTests(unittest.TestCase):
    def test_a_string_body_is_verified_as_its_utf8_bytes(self) -> None:
        case = case_named("valid_non_ascii_body")
        self.assertIsNotNone(case["body"])
        result = verify_webhook_signature(
            raw_body=case["body"],
            signature_header=case["headers"]["x-grualink-signature"],
            secret=case["secret"],
            now_seconds=case["now"],
        )
        self.assertEqual(result, SignatureVerificationResult(valid=True, reason="ok"))

    def test_bytearray_and_memoryview_bodies_are_accepted(self) -> None:
        case = case_named("valid_basic")
        raw_body = base64.b64decode(case["body_base64"])
        for body in (bytearray(raw_body), memoryview(raw_body)):
            result = verify_webhook_signature(
                raw_body=body,
                signature_header=case["headers"]["x-grualink-signature"],
                secret=case["secret"],
                now_seconds=case["now"],
            )
            self.assertTrue(result.valid)

    def test_the_receiver_may_hold_two_secrets_during_its_own_rotation(self) -> None:
        case = case_named("valid_basic")
        raw_body = base64.b64decode(case["body_base64"])
        header = case["headers"]["x-grualink-signature"]
        ok = verify_webhook_signature(
            raw_body=raw_body,
            signature_header=header,
            secret=["whsec_TEST_ONLY_FAKE_NEXT", case["secret"]],
            now_seconds=case["now"],
        )
        self.assertTrue(ok.valid)
        none = verify_webhook_signature(
            raw_body=raw_body, signature_header=header, secret=[], now_seconds=case["now"]
        )
        self.assertEqual(none, SignatureVerificationResult(valid=False, reason="missing_secret"))

    def test_the_default_clock_is_the_system_clock(self) -> None:
        # The vectors are signed for a fixed instant, so against the real clock they are stale.
        case = case_named("valid_basic")
        result = verify_webhook_signature(
            raw_body=base64.b64decode(case["body_base64"]),
            signature_header=case["headers"]["x-grualink-signature"],
            secret=case["secret"],
        )
        self.assertEqual(result.reason, "timestamp_out_of_tolerance")

    def test_a_trailing_line_break_in_the_timestamp_or_signature_is_not_accepted(self) -> None:
        body = b"{}"
        secret = "whsec_TEST_ONLY_FAKE_SECRET_0001"
        signature = compute_webhook_signature(secret, 1795000000, body)
        for header in (f"v1={signature},t=1795000000\n", f"t=1795000000,v1={signature}\x0b"):
            # Surrounding whitespace is trimmed, so these two still verify...
            self.assertTrue(
                verify_webhook_signature(
                    raw_body=body, signature_header=header, secret=secret, now_seconds=1795000000
                ).valid
            )
        for header in (f"t=1795000000\n1,v1={signature}", f"t=1795000000,v1={signature}\n0"):
            # ...but a line break inside a value never does.
            result = verify_webhook_signature(
                raw_body=body, signature_header=header, secret=secret, now_seconds=1795000000
            )
            self.assertEqual(result.reason, "malformed_header")
        arabic_digits = "t=١٧٩٥٠٠٠٠٠٠,v1=" + signature
        result = verify_webhook_signature(
            raw_body=body, signature_header=arabic_digits, secret=secret, now_seconds=1795000000
        )
        self.assertEqual(result.reason, "malformed_header")

    def test_verification_never_raises_on_hostile_input(self) -> None:
        hostile: list[Any] = [
            None,
            42,
            {},
            [],
            b"t=1,v1=" + b"a" * 64,
            "x" * 100_000,
            "t=1,v1=" + "a" * 64 + "," * 5000,
        ]
        for header in hostile:
            for secret in (None, 42, b"whsec_bytes", "whsec_TEST_ONLY_FAKE_SECRET_0001"):
                for now in (1, None, "soon", float("nan")):
                    result = verify_webhook_signature(
                        raw_body=b"{}",
                        signature_header=header,
                        secret=secret,  # type: ignore[arg-type]
                        now_seconds=now,  # type: ignore[arg-type]
                    )
                    self.assertFalse(result.valid)
        bad_body = verify_webhook_signature(
            raw_body=None,  # type: ignore[arg-type]
            signature_header="t=1,v1=" + "a" * 64,
            secret="whsec_TEST_ONLY_FAKE_SECRET_0001",
            now_seconds=1,
        )
        self.assertFalse(bad_body.valid)

    def test_build_signature_header_produces_what_the_verifier_accepts(self) -> None:
        body = b'{"id": "x"}'
        secrets = ["whsec_TEST_ONLY_FAKE_A", "whsec_TEST_ONLY_FAKE_B"]
        header = build_signature_header(secrets, 1795000000, body)
        self.assertRegex(header, r"^t=1795000000,v1=[0-9a-f]{64},v1=[0-9a-f]{64}$")
        for secret in secrets:
            result = verify_webhook_signature(
                raw_body=body, signature_header=header, secret=secret, now_seconds=1795000001
            )
            self.assertTrue(result.valid)

    def test_compute_webhook_signature_matches_the_vectors(self) -> None:
        case = case_named("valid_basic")
        signature = compute_webhook_signature(
            case["secret"], 1795000000, base64.b64decode(case["body_base64"])
        )
        self.assertEqual(case["headers"]["x-grualink-signature"], f"t=1795000000,v1={signature}")


if __name__ == "__main__":
    unittest.main()

"""End-to-end example: request a tow truck with fictitious data, print the quote and check the status.

    GRUALINK_API_KEY=<your TEST key> python -m examples.request_tow

Use a TEST key (glk_test_...). Test requests never become real services.
"""

from __future__ import annotations

import os
import sys
import uuid

from grualink import (
    GrualinkApiError,
    GrualinkClient,
    GrualinkError,
    GrualinkValidationError,
    RetryInfo,
    ServiceRequest,
    ServiceRequestInput,
)


def print_quote(service_request: ServiceRequest) -> None:
    quote = service_request["quote"]
    price = quote.get("estimated_price")
    if not price:
        reason = quote.get("unpriced_reason") or "unknown reason"
        print(f"  No estimated price ({reason}); the company will quote it.")
        return
    print(f"  Subtotal:  {price['subtotal']:.2f} {price['currency']}")
    for tax in price["taxes"]:
        sign = "-" if tax["type"] == "retention" else "+"
        print(f"  {sign} {tax['name']} ({tax['rate']}%): {tax['amount']:.2f}")
    print(f"  Total:     {price['total']:.2f} {price['currency']} (estimate, extras not included)")


def on_retry(info: RetryInfo) -> None:
    name = type(info.error).__name__
    print(
        f"Attempt {info.attempt} failed ({name}); retrying in {info.delay_seconds:.2f} s",
        file=sys.stderr,
    )


def main() -> int:
    api_key = os.environ.get("GRUALINK_API_KEY", "")
    if not api_key:
        print(
            "Set GRUALINK_API_KEY to your TEST key (it starts with glk_test_). See .env.example.",
            file=sys.stderr,
        )
        return 1
    if not api_key.startswith("glk_test_"):
        print("This example only runs with a TEST key (glk_test_...).", file=sys.stderr)
        return 1

    # With a test key, the prefix of external_ref decides the final status: TEST-CONFIRMAR ->
    # confirmed, TEST-EN-CURSO -> in_progress, TEST-TERMINAR -> completed, TEST-RECHAZAR -> rejected,
    # TEST-CANCELAR -> cancelled.
    external_ref = f"TEST-CONFIRMAR-{uuid.uuid4().hex[:8]}"
    request: ServiceRequestInput = {
        "external_ref": external_ref,
        "contact": {"name": "Persona de Ejemplo", "phone": "+525500000000"},
        "vehicle": {
            "make": "Nissan",
            "model": "Versa",
            "year": 2019,
            "color": "Gris",
            "plate": "ABC123A",
            "type": "auto",
        },
        "pickup": {
            "address": "Av. Juárez 100, Centro, Guadalajara, Jal.",
            "lat": 20.6767,
            "lng": -103.3475,
        },
        "destination": {
            "address": "Av. Patria 500, Zapopan, Jal.",
            "lat": 20.7214,
            "lng": -103.3918,
        },
        "service": {"reason": "No arranca", "category": "vial", "required_class": "A"},
        "notes": "Datos ficticios de ejemplo.",
    }

    try:
        client = GrualinkClient(
            api_key,
            base_url=os.environ.get("GRUALINK_API_BASE_URL") or None,
            on_retry=on_retry,
        )

        health = client.health()
        print(f"API status: {health['status']} ({health['version']})")

        # In your system, store this key next to your own record BEFORE calling, and reuse it if you
        # have to retry.
        idempotency_key = str(uuid.uuid4())

        created = client.create_service_request(request, idempotency_key=idempotency_key)
        sr = created.service_request
        print(
            f'\nCreated {sr["id"]} ({sr["mode"]}) with status "{sr["status"]}", '
            f"reference {sr['external_ref']}"
        )
        print_quote(sr)

        # Sending it again with the same key and the same body creates nothing new.
        again = client.create_service_request(request, idempotency_key=idempotency_key)
        same_id = again.service_request["id"] == sr["id"]
        print(f"\nSent again with the same Idempotency-Key: replayed={again.replayed}, same id={same_id}")

        current = client.get_service_request(sr["id"])
        service = current.get("service")
        folio = f", folio {service['folio']}" if service and service.get("folio") else ""
        print(f'\nStatus now: "{current["status"]}"{folio}')

        by_ref = client.find_service_requests_by_external_ref(external_ref)
        print(f"Requests with reference {external_ref}: {len(by_ref)}")
    except GrualinkValidationError as error:
        fields = ", ".join(f"{d['field']} ({d['issue']})" for d in error.details)
        print(f"Invalid fields: {fields}", file=sys.stderr)
        return 1
    except GrualinkApiError as error:
        print(f"API error {error.status} {error.code}: {error.message}", file=sys.stderr)
        return 1
    except GrualinkError as error:
        print(f"{type(error).__name__}: {error.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

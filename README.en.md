# GRUALINK API: Python example

[Español](README.md)

A clone-and-run example that integrates your system (insurer, roadside assistance, integrator) with the
GRUALINK API: **request a tow truck**, **check its status** and **receive signed webhooks**.

Public API guide: <https://grualink.mx/api>

What is inside:

- A **minimal client** (`grualink/client.py`): creates requests with `Idempotency-Key`, reads the status,
  typed errors, retries with exponential backoff and a timeout. Standard library only: there are no
  runtime dependencies.
- A **webhook receiver** (`grualink/webhooks.py`, `grualink/webhook_server.py`): verifies the signature
  over the raw body, checks the time window, supports secret rotation and drops duplicates.
- Two **runnable examples** (`examples/`).
- The **contract kit** (`contract/`): the API contract in Spanish and English and the signature test
  cases shared by the examples in every language.

It is not an SDK: it is code meant to be read and copied into your project.

## Requirements

- Python 3.10 or later.
- A **test key** (`glk_test_…`). The towing company you work with gives it to you.
- For webhooks: the **signing secret** (`whsec_…`) the same company gives you when it registers the
  address of your system.

## Installation

```bash
git clone <this repository's address>
cd grualink-api-python
```

There is nothing to install to run the examples or the tests. For linting and type checking (optional),
create a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

## Environment variables

Names only; the values live on your machine and never in the repository. There is a template in
`.env.example` (the examples read the variables from the environment; they do not load that file by
themselves).

| Variable | Purpose |
|---|---|
| `GRUALINK_API_KEY` | Your **test** key (`glk_test_…`) |
| `GRUALINK_WEBHOOK_SECRET` | Webhook signing secret (`whsec_…`), whole, with its prefix |
| `GRUALINK_WEBHOOK_SECRET_PREVIOUS` | Optional: the previous secret while a rotation is in progress |
| `GRUALINK_API_BASE_URL` | Optional: another base URL (default `https://grualink.mx/api/v1`) |
| `PORT` | Optional: port of the example receiver (default 8787) |

## Running the examples

From the root of the repository.

### Request a tow truck

```bash
export GRUALINK_API_KEY=...   # your test key
python -m examples.request_tow
```

With fictitious data, the example sends a request, prints the estimated price, sends it again with the
same `Idempotency-Key` to show nothing is duplicated, and reads its status. It only runs with test keys.

With a test key, the start of `external_ref` decides the final status: `TEST-CONFIRMAR` (confirmed),
`TEST-EN-CURSO` (in progress), `TEST-TERMINAR` (completed), `TEST-RECHAZAR` (rejected), `TEST-CANCELAR`
(cancelled).

### Receive webhooks

```bash
export GRUALINK_WEBHOOK_SECRET=...   # your signing secret
python -m examples.webhook_server
```

It listens on `http://localhost:8787/webhooks/grualink`. GRUALINK only delivers to public `https`
addresses on port 443: publish the receiver behind your domain (or an `https` tunnel while testing), give
that address to the company and ask for a test event.

## Using it in your code

```python
import os

from grualink import GrualinkClient, GrualinkValidationError

client = GrualinkClient(os.environ["GRUALINK_API_KEY"])

try:
    result = client.create_service_request(
        {
            "external_ref": "TEST-CONFIRMAR-0001",
            "contact": {"name": "Persona de Ejemplo", "phone": "+525500000000"},
            "pickup": {
                "address": "Av. Juárez 100, Centro, Guadalajara, Jal.",
                "lat": 20.6767,
                "lng": -103.3475,
            },
            "destination": {"address": "Av. Patria 500, Zapopan, Jal.", "lat": 20.7214, "lng": -103.3918},
        },
        idempotency_key=my_record.idempotency_key,  # store it before calling
    )
    service_request = result.service_request
    print(
        service_request["id"],
        service_request["status"],
        service_request["quote"]["estimated_price"],
        result.replayed,
    )
except GrualinkValidationError as error:
    print(error.details)
```

The operations are `create_service_request`, `get_service_request`,
`find_service_requests_by_external_ref` and `health`. Responses are the dictionaries parsed from JSON,
with the field names of the contract; `grualink/types.py` describes them for your editor and your type
checker.

The client is synchronous. Times are expressed in seconds (`timeout_seconds`, `retry_base_delay_seconds`,
`retry_max_delay_seconds`).

### Idempotency and retries

- Every request carries an `Idempotency-Key` of your own. If you repeat the call with the same key and
  the same body within 24 hours, the API answers **`200`** with `Idempotent-Replayed: true` and creates
  nothing (`replayed` is `True`). The same key with another body answers `409`.
- The client retries only what makes sense: network errors, timeouts, `5xx` and `429 rate_limited`
  (waiting what `Retry-After` says). It uses exponential backoff with jitter and always resends the same
  key and the same bytes.
- Every attempt has a timeout (15 seconds by default) and a redirect is never followed.
- The key never appears in errors or logs.

### Verifying a webhook

```python
import os

from grualink import verify_webhook_signature

result = verify_webhook_signature(
    raw_body=raw_body,  # the bytes exactly as received
    signature_header=headers.get("x-grualink-signature"),
    secret=os.environ["GRUALINK_WEBHOOK_SECRET"],  # whole, with whsec_
)
if not result.valid:
    ...  # answer 401 with no detail; result.reason is for your logs
```

What matters:

1. Verify the **raw body**, before parsing it as JSON. If you parse and serialize it again, the
   signature no longer matches.
2. Use the **whole** secret, with its `whsec_` prefix.
3. Reject timestamps more than 5 minutes away from your clock.
4. After a secret rotation two `v1` signatures arrive for 24 hours: accept if either matches.
5. Deliveries can repeat: keep the `id` of the ones you processed and answer `2xx` without reprocessing.
6. Answer `2xx` within 10 seconds and do the heavy work afterwards.

### Mounting it on Flask, FastAPI or Django

The delicate part is getting the **raw body**. `create_webhook_handler` depends on no framework: it takes
the bytes and the headers and returns the status and body to answer with.

```python
from grualink import create_webhook_handler

handle = create_webhook_handler(secret=os.environ["GRUALINK_WEBHOOK_SECRET"], on_event=on_event)
```

Flask: use `request.get_data()` (not `request.json` or `request.get_json()`, which hand you the parsed
body).

```python
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024


@app.post("/webhooks/grualink")
def grualink_webhook():
    result = handle(request.get_data(), dict(request.headers))
    return result.body, result.status
```

FastAPI: take the `Request` and read `await request.body()` (do not declare a model as a parameter:
FastAPI would parse the body first).

```python
@app.post("/webhooks/grualink")
async def grualink_webhook(request: Request):
    result = handle(await request.body(), dict(request.headers))
    return JSONResponse(result.body, status_code=result.status)
```

Django: use `request.body` (bytes) and exempt the view from CSRF, because the signature is the
authentication.

```python
@csrf_exempt
@require_POST
def grualink_webhook(request):
    result = handle(request.body, dict(request.headers))
    return JsonResponse(result.body, status=result.status)
```

In all three, cap the body at 64 KB in your server or proxy, and make `on_event` only store or enqueue
the event.

`InMemoryDeliveryDeduplicator` works for a single process. With several processes or instances (the
usual setup behind gunicorn or uvicorn), replace it with one backed by your database: a unique key on
the event `id` is enough, with the methods `claim(delivery_id)` and `release(delivery_id)`.

## Errors

Every API error has the shape `{ "error": { "code", "message", "details"? } }`. `code` is stable; branch
on it, not on `message` (which is written in Spanish and may change).

| HTTP | `code` | What happened | Class | Retried? |
|---|---|---|---|---|
| 400 | `invalid_request` | The request is not valid | `GrualinkInvalidRequestError` | no |
| 400 | `invalid_json` | The body is not valid JSON | `GrualinkInvalidRequestError` | no |
| 400 | `key_in_query` | The key was sent in the URL; have it revoked | `GrualinkInvalidRequestError` | no |
| 400 | `idempotency_key_required` | `Idempotency-Key` is missing | `GrualinkInvalidRequestError` | no |
| 400 | `invalid_idempotency_key` | Invalid `Idempotency-Key` (1 to 255 visible ASCII characters) | `GrualinkInvalidRequestError` | no |
| 401 | `unauthorized` | Key missing, invalid, expired or revoked | `GrualinkAuthenticationError` | no |
| 403 | `insufficient_scope` | The key is not allowed to do this | `GrualinkPermissionError` | no |
| 403 | `customer_unavailable` | Your customer account with the company is unavailable | `GrualinkPermissionError` | no |
| 403 | `live_mode_unavailable` | Live requests are not enabled yet | `GrualinkPermissionError` | no |
| 403 | `browser_not_allowed` | Live key used from a browser | `GrualinkPermissionError` | no |
| 404 | `not_found` | It does not exist or is not yours | `GrualinkNotFoundError` | no |
| 405 | `method_not_allowed` | Method not allowed for that path | `GrualinkInvalidRequestError` | no |
| 409 | `idempotency_conflict` | That `Idempotency-Key` was already used with another body | `GrualinkIdempotencyConflictError` | no |
| 413 | `payload_too_large` | The body exceeds 16 KB | `GrualinkInvalidRequestError` | no |
| 415 | `unsupported_media_type` | The body must be `application/json` | `GrualinkInvalidRequestError` | no |
| 422 | `validation_error` | Invalid fields; `details` says which | `GrualinkValidationError` | no |
| 429 | `rate_limited` | Too many requests; wait `Retry-After` | `GrualinkRateLimitError` | yes |
| 429 | `too_many_open_requests` | Too many unresolved requests with the company | `GrualinkRateLimitError` | no |
| 500 | `internal_error` | Internal error | `GrualinkServerError` | yes |

API errors (`GrualinkApiError`) carry `status`, `code`, `message`, `details`, `retry_after_seconds` and
`attempts`.

Also: `GrualinkConnectionError` (no response, retried), `GrualinkTimeoutError` (timed out, retried) and
`GrualinkConfigError` (invalid configuration or argument; nothing was sent). All of them inherit from
`GrualinkError`.

The full contract, with fields, limits and the order of checks, is in [`contract/API.md`](contract/API.md).

## Tests

```bash
python -m unittest                 # the tests; standard library only
python scripts/check_contract.py   # contract/ is identical to the shared kit
```

With the development virtual environment:

```bash
ruff check . && ruff format --check .
mypy
```

The tests never call the real API: they use a local fake server. They cover every case of
`contract/signature-vectors.json`, each error code, retries, idempotency and that the key appears in no
error or log output.

## Layout

```
contract/     API contract (es/en), signature test cases and checklist
grualink/     client, errors, types, webhook verification and receiver
examples/     request_tow.py and webhook_server.py
tests/        tests (unittest) and fake server
scripts/      check that contract/ did not change
```

## License

[MIT](LICENSE)

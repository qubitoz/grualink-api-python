# GRUALINK API: public contract (v1)

This document is the contract every example repository implements, in any language. It describes only
what an integrator can observe from the outside. Spanish version: [API.es.md](API.es.md). Public guide:
<https://grualink.mx/api>.

- Base URL: `https://grualink.mx/api/v1`
- Format: JSON, UTF-8. Timestamps are ISO 8601 in UTC.
- Field names, codes and enum values are the same in every language. Do not translate them.

## 1. Authentication

Every request except `GET /health` carries the key issued by the towing company you work with:

```
Authorization: Bearer glk_test_...
```

| Key | Use |
|---|---|
| `glk_test_...` | Integration and tests. Its requests never become real services. |
| `glk_live_...` | Production. Secret: server side only. |

Rules:

- The key goes **only** in the `Authorization` header. A request with the key in the URL (or a query
  parameter named `key`, `api_key`, `token`, `access_token`, `auth` or `authorization`) is rejected with
  `400 key_in_query`, also on `/health`.
- A missing, invalid, expired or revoked key always gets the same `401 unauthorized`, with
  `WWW-Authenticate: Bearer`.
- A live key sent from a browser (the request has an `Origin` header) gets `403 browser_not_allowed`.
- Never log the key, never put it in an error message, never commit it. Read it from the environment
  variable `GRUALINK_API_KEY`.

## 2. Endpoints

| Method | Path | What it does |
|---|---|---|
| `GET` | `/health` | Checks the service answers. No key. `200 {"status":"ok","version":"v1"}` |
| `POST` | `/service-requests` | Sends a service request. `201` (created) or `200` (replayed) |
| `GET` | `/service-requests/{id}` | Returns one service request. `200` |
| `GET` | `/service-requests?external_ref=...` | Finds your requests by your own reference (up to 20, newest first). `200` |

A path that exists called with another method answers `405 method_not_allowed`; any other path answers
`404 not_found`.

## 3. Create a service request

`POST /service-requests`

### Request headers

| Header | Required | Value |
|---|---|---|
| `Authorization` | yes | `Bearer <key>` |
| `Content-Type` | yes | `application/json` (parameters such as `; charset=utf-8` are accepted) |
| `Idempotency-Key` | yes | 1 to 255 visible ASCII characters (`0x21` to `0x7E`, no spaces). See section 4 |

### Body

A JSON object of at most **16 KB** (16,384 bytes). Only the fields below are accepted: an unknown field,
at any level, is rejected with `validation_error` (`unknown_field`). `null` is the same as omitting the
field.

| Field | Type | Required | Limits |
|---|---|---|---|
| `external_ref` | string | no (1) | up to 64 characters. Your reference (claim, case). |
| `contact` | object | **yes** | |
| `contact.name` | string | **yes** | up to 120 characters |
| `contact.phone` | string | **yes** | up to 40 characters as sent. International format (`+52...`); see phone rules |
| `pickup` | object | **yes** | |
| `pickup.address` | string | **yes** | up to 500 characters |
| `pickup.lat` | number | no (2) | -90 to 90 |
| `pickup.lng` | number | no (2) | -180 to 180 |
| `destination` | object | no | |
| `destination.address` | string | **yes** if `destination` is sent | up to 500 characters |
| `destination.lat` / `destination.lng` | number | no (2) | same ranges |
| `vehicle` | object | no | |
| `vehicle.make` | string | no | up to 40 characters |
| `vehicle.model` | string | no | up to 60 characters |
| `vehicle.year` | integer | no | 1900 to current year + 2. A 4-digit string is also accepted |
| `vehicle.color` | string | no | up to 30 characters |
| `vehicle.plate` | string | no | stored uppercase without spaces or dots; then 1 to 15 of `A-Z 0-9 -` |
| `vehicle.type` | string | no | `auto`, `camioneta`, `motocicleta`, `camion`, `otro` |
| `service` | object | no | |
| `service.reason` | string | no | up to 200 characters |
| `service.category` | string | no | `vial`, `colision` |
| `service.required_class` | string | no | `A`, `B`, `C`, `D` |
| `service.required_accessories` | array of string | no | up to 12, no duplicates. Values: `dolly`, `winch`, `cadenas`, `eslingas`, `patines`, `rampas`, `barra_tiro`, `extension_pluma`, `gato_hidraulico`, `pasacorriente`, `compresor`, `luces_remolque` |
| `notes` | string | no | up to 1,000 characters and 20 lines |

(1) Required when your account with the company requires a case number. When it is missing in that
case the answer is `422 validation_error` with `{"field":"external_ref","issue":"invalid_value"}`.

(2) `lat` and `lng` go together: sending only one is an error (`required` on the missing one). The pair
`0, 0` is rejected (`invalid_value` on `pickup` or `destination`). Without coordinates for both pickup and
destination the request is accepted but not priced.

Text rules (all string fields):

- Text is trimmed and normalized (Unicode NFC). Length is counted in characters (code points), after
  trimming.
- A blank optional string counts as absent; a blank required string is `required`.
- Control characters and bidirectional-override marks are rejected (`invalid_format`). `notes` may
  contain line breaks (`\n`; `\r\n` and `\r` are converted).

Phone rules (`contact.phone`): separators are ignored and a leading `tel:`, `sms:` or `whatsapp:` is
removed. 10 digits are taken as a Mexican number (`+52`); `00` at the start is read as `+`. The result
must be `+` followed by 10 to 15 digits, not starting with 0; otherwise `invalid_format`.

### Response

`201` with the service request (section 5). `200` with header `Idempotent-Replayed: true` when the
request is a repetition (section 4).

## 4. Idempotency

- `Idempotency-Key` is yours: one new value per service request you want to create (a UUID is fine).
  Store it with your record **before** calling.
- Repeating the call with the **same key and the same body** within 24 hours creates nothing and returns
  the original service request with **`200`** and the header **`Idempotent-Replayed: true`** (the first
  call answered `201`).
- "Same body" means the **same bytes**. Serialize the body once and resend those bytes; do not rebuild
  the JSON on a retry (a different key order or spacing is a different body).
- The same key with a different body answers `409 idempotency_conflict`.
- Keys are scoped to your API key and remembered for 24 hours.
- Only a successful creation is remembered. A call that ended in an error can be sent again with the
  same key.

## 5. The service request object

```json
{
  "id": "11111111-2222-4333-8444-555555555555",
  "object": "service_request",
  "status": "quoted",
  "mode": "test",
  "external_ref": "TEST-CONFIRMAR-0001",
  "created_at": "2026-11-18T10:00:00.000Z",
  "resolved_at": null,
  "rejection_reason": null,
  "service": null,
  "quote": {
    "estimated_price": {
      "currency": "MXN",
      "subtotal": 864.33,
      "taxes": [{ "name": "IVA", "type": "tax", "rate": 16, "amount": 138.29 }],
      "tax_total": 138.29,
      "retention_total": 0,
      "total": 1002.62
    },
    "includes_extras": false,
    "approximate_distance": true,
    "unpriced_reason": null,
    "note": "Precio estimado. No incluye extras como casetas o maniobras; la empresa confirma el precio final."
  },
  "assignment": null,
  "timeline": null
}
```

| Field | Type | Notes |
|---|---|---|
| `id` | string (UUID) | |
| `object` | string | always `service_request` |
| `status` | string | see statuses |
| `mode` | string | `test` or `live`, from the key |
| `external_ref` | string or null | |
| `created_at` | string | |
| `resolved_at` | string or null | |
| `rejection_reason` | string or null | set when `status` is `rejected` |
| `service` | object or null | `{ "folio": string or null, "status": string }` once the company registered the service |
| `quote.estimated_price` | object or null | `null` when it could not be priced; see `unpriced_reason` |
| `quote.estimated_price.taxes[].type` | string | `tax` (added) or `retention` (subtracted) |
| `quote.includes_extras` | boolean | always `false`: the estimate never includes extras such as tolls |
| `quote.approximate_distance` | boolean | |
| `quote.unpriced_reason` | string or null | `missing_coordinates`, `missing_destination`, `no_zones_configured`, `missing_vehicle_class` |
| `assignment` | object or null | `{ "operator_first_name": null, "truck": { "economic_number", "plate" } }`. Live keys only |
| `timeline` | object or null | any of `on_the_way_at`, `on_site_at`, `transport_started_at`, `completed_at`. Live keys only |

Treat unknown fields as forward compatible: ignore what you do not know.

### Statuses

`quoted` → `confirmed` → `in_progress` → `completed`, or it ends in `rejected`, `cancelled` or `expired`.

| Status | Meaning |
|---|---|
| `received` | Received; it could not be priced automatically and the company quotes it by hand |
| `quoted` | The company has it as a quote |
| `confirmed` | The company confirmed it |
| `in_progress` | The tow truck is on its way or the service is in progress |
| `completed` | Service finished |
| `rejected` | Rejected by the company; see `rejection_reason` |
| `cancelled` | Cancelled |
| `expired` | Test mode only: nobody resolved it in 24 hours |

### Test mode

With a `glk_test_` key the request is validated and priced like a real one, but the prefix of
`external_ref` decides its final status:

| `external_ref` starts with | Final status |
|---|---|
| `TEST-CONFIRMAR` | `confirmed` |
| `TEST-EN-CURSO` | `in_progress` |
| `TEST-TERMINAR` | `completed` |
| `TEST-RECHAZAR` | `rejected` |
| `TEST-CANCELAR` | `cancelled` |
| anything else or nothing | `quoted` |

## 6. Read

`GET /service-requests/{id}` returns the object of section 5. An id that is not yours, does not exist
or is not a UUID answers `404 not_found`.

`GET /service-requests?external_ref=<reference>` returns `{ "object": "list", "data": [ ... ] }` with up
to 20 of your requests, newest first (empty list when there is none). `external_ref` is the only
accepted parameter: another one answers `400 invalid_request` (`unknown_field`); missing or blank answers
`400 invalid_request` (`required`); longer than 64 characters answers `422 validation_error` (`too_long`).

## 7. Errors

Every error has this shape. `code` is stable; `message` is written for people, is in Spanish and may
change. Branch on `code` (and the HTTP status), never on `message`.

```json
{ "error": { "code": "validation_error", "message": "Hay campos inválidos.",
             "details": [{ "field": "contact.phone", "issue": "invalid_format" }] } }
```

`details` (up to 20 entries) appears on `validation_error` and on some `invalid_request`. `issue` is one
of `required`, `invalid_type`, `too_long`, `invalid_format`, `out_of_range`, `unknown_field`,
`invalid_value`, `duplicate`. When the body is not a JSON object, `field` is `body`.

| HTTP | `code` | What happened | Retry? |
|---|---|---|---|
| 400 | `invalid_request` | The request is not valid | no |
| 400 | `invalid_json` | The body is not valid JSON (or not valid UTF-8) | no |
| 400 | `key_in_query` | The key was sent in the URL. Have it revoked | no |
| 400 | `idempotency_key_required` | `Idempotency-Key` header missing | no |
| 400 | `invalid_idempotency_key` | `Idempotency-Key` is not 1 to 255 visible ASCII characters | no |
| 401 | `unauthorized` | Key missing, invalid, expired or revoked | no |
| 403 | `insufficient_scope` | The key is not allowed to do this | no |
| 403 | `customer_unavailable` | Your customer account with the company is not available | no |
| 403 | `live_mode_unavailable` | Live requests are not enabled yet; use a test key | no |
| 403 | `browser_not_allowed` | A live key was used from a browser | no |
| 404 | `not_found` | It does not exist or is not yours | no |
| 405 | `method_not_allowed` | Method not allowed for this path | no |
| 409 | `idempotency_conflict` | The `Idempotency-Key` was already used with another body | no |
| 413 | `payload_too_large` | The body exceeds 16 KB | no |
| 415 | `unsupported_media_type` | The body must be sent as `application/json` | no |
| 422 | `validation_error` | Invalid fields; see `details` | no |
| 429 | `rate_limited` | Too many requests. Wait `Retry-After` seconds | **yes** |
| 429 | `too_many_open_requests` | Too many unresolved requests with this company | no (3) |
| 500 | `internal_error` | Internal error | **yes**, same `Idempotency-Key` |

(3) It clears when the company resolves some of your open requests, not after a few seconds. Report it;
do not loop.

### Order of checks

When a request has several problems, this is the one you get. Tests in every language rely on it.

For any request:

1. `404 not_found` / `405 method_not_allowed` (decided before looking at the key)
2. `400 key_in_query`
3. `403 browser_not_allowed`
4. `401 unauthorized`, or `429 rate_limited`

Then, for `POST /service-requests`:

5. `415 unsupported_media_type`
6. `400 idempotency_key_required`, then `400 invalid_idempotency_key`
7. `413 payload_too_large`
8. `400 invalid_json`
9. `422 validation_error` (field rules of section 3)
10. `403 insufficient_scope`, then `403 live_mode_unavailable`
11. idempotency: `200` replay, or `409 idempotency_conflict`
12. `403 customer_unavailable`, or `422 validation_error` for account rules (`external_ref` required)
13. `429 too_many_open_requests`
14. `201`

So a `409` only appears for a body that already passed validation, and a replay is answered even when
the open-request limit is reached.

## 8. Limits

- 60 requests per minute per key (plus a shared ceiling per company). Beyond it: `429 rate_limited`
  with `Retry-After` in seconds (1 to 60).
- Requests without a valid key are limited to 20 per minute per IP address: repeated `401` turn into
  `429`.
- Open (unresolved) requests are capped, counted separately for test and live. Test: 20 per key and 50
  per company. Live: 50 per key and 200 per company by default (the company can raise it).
- Body: 16 KB. `Idempotency-Key` memory: 24 hours. An unresolved test request expires after 24 hours.

## 9. Recommended client behaviour

- **Timeout**: set one on every request (the examples use 15 seconds per attempt).
- **Retry** only: network errors, timeouts, `5xx` and `429 rate_limited`. Never retry other `4xx`.
- **Backoff**: exponential with full jitter (`random(0, min(cap, base * 2^n))`; the examples use base
  0.5 s, cap 8 s, 3 retries). When the answer has `Retry-After`, wait at least that long.
- **Idempotency**: every retry of a `POST` resends the same `Idempotency-Key` and the same bytes.
- **Redirects**: do not follow them; the key must not travel to another address.
- **Secrets**: the key never appears in logs, error messages or exceptions, including when the server
  or a proxy echoes it back.

## 10. Webhooks

Instead of polling, you can receive a notification when a request changes. Give the company the
address of your system and choose the events; the company gives you a **signing secret** that starts
with `whsec_`.

Address requirements: `https`, default port (443), a public domain name (no IP addresses, no internal
names), no credentials, at most 500 ASCII characters.

### Delivery

A `POST` to your address with these headers (header names are case-insensitive):

| Header | Value |
|---|---|
| `Content-Type` | `application/json; charset=utf-8` |
| `User-Agent` | `GRUALINK-Avisos/1.0` |
| `X-GruaLink-Event` | The event, same as `type` in the body |
| `X-GruaLink-Delivery` | Id of the event, same as `id` in the body and identical on every retry |
| `X-GruaLink-Timestamp` | Seconds since 1970 (UTC) when this attempt was signed |
| `X-GruaLink-Mode` | `test` or `live` |
| `X-GruaLink-Signature` | `t=<seconds>,v1=<signature>[,v1=<signature>]` |

Body (at most 64 KB):

```json
{
  "id": "aaaaaaaa-0000-4000-8000-000000000001",
  "object": "event",
  "type": "service_request.confirmed",
  "mode": "test",
  "created_at": "2026-11-18T10:05:01.000Z",
  "data": {
    "previous_status": "quoted",
    "service_request": { "...": "the object of section 5, as GET /service-requests/{id} returns it" }
  }
}
```

The example is formatted for reading. The real body has its own spacing and key order: never assume it
matches what your JSON library would write.

| `type` | When |
|---|---|
| `service_request.quoted` | The company has it as a quote |
| `service_request.confirmed` | The company confirmed it |
| `service_request.in_progress` | On its way or in progress |
| `service_request.completed` | Finished |
| `service_request.rejected` | Rejected (with the reason) |
| `service_request.cancelled` | Cancelled |
| `service_request.expired` | Test mode only |
| `service_request.assigned` | Operator and truck assigned or changed. `previous_status` is `null` |
| `service_request.on_the_way` | The operator left for the pickup. `previous_status` is `null` |
| `service_request.on_site` | The operator arrived. `previous_status` is `null` |
| `webhook.test` | Test event sent on request. `data` is `{ "message": "..." }` |

Ignore event types you do not know.

### Verifying the signature

The signature is `HMAC-SHA256`, in lowercase hexadecimal, of the bytes `<t>.<raw body>`, keyed with
your secret **exactly as issued, including its `whsec_` prefix**.

Implement it as a pure function `(raw body, signature header, secret, now, tolerance) → (valid, reason)`
that never throws, with these steps **in this order**:

1. If there is no secret (empty or not configured): reject, `missing_secret`.
2. If the `X-GruaLink-Signature` header is absent or blank: reject, `missing_header`.
3. Split the header on `,`; trim each part; split each part at its first `=` into name and value.
   - `t`: the first part named `t`. It must be 1 to 12 decimal digits.
   - candidates: every part named `v1` whose value is exactly 64 lowercase hexadecimal characters.
     Other `v1` values and parts with other names are ignored.
   - If `t` is missing or malformed, or there is no candidate: reject, `malformed_header`.
4. If `|now - t| > tolerance` (default **300 seconds**, limit inclusive): reject,
   `timestamp_out_of_tolerance`. `now` is in seconds and must be injectable for tests.
5. Compute `expected = hex(HMAC-SHA256(key = secret bytes, message = t + "." + raw body bytes))`, with
   `t` as the digits received and the body as the **raw bytes received**, before any parsing.
6. Accept (`ok`) if **any** candidate equals `expected`, comparing in **constant time**. Otherwise
   reject, `signature_mismatch`.

The reasons are exactly: `ok`, `missing_secret`, `missing_header`, `malformed_header`,
`timestamp_out_of_tolerance`, `signature_mismatch`.

Common mistakes, all covered by `signature-vectors.json`:

- Verifying the body after parsing and serializing it again. The bytes change and the signature fails.
- Removing the `whsec_` prefix or decoding the secret. Use it as text, whole.
- Decoding the body with the wrong charset. Sign bytes, not text.
- Comparing with `==`. Use the constant-time comparison of your platform.
- Accepting uppercase hex, or lowercasing the header before comparing.
- Skipping the timestamp check, which is what prevents an old delivery from being replayed.

### Secret rotation

After the company rotates the secret, deliveries carry **two** `v1` values for 24 hours: the first made
with the new secret, the second with the previous one. Accept the delivery if any `v1` matches the
secret you hold. This lets you switch secrets at any moment within those 24 hours.

### Answering, retries and duplicates

- Answer any `2xx` within **10 seconds**. Anything else (another status, a redirect, no answer in time)
  counts as a failure. Do the slow work after answering.
- A failed delivery is retried after 1 min, 5 min, 30 min, 2 h, 6 h and 12 h: up to 7 attempts within 24
  hours. Each attempt is signed again (new `t`), with the same `id`.
- After 20 consecutive failed deliveries the webhook is switched off; ask the company to switch it on
  again.
- Deliveries can arrive **more than once** and, rarely, **out of order**. Deduplicate by the event id:
  keep the ids you processed (for at least 24 hours) and answer `2xx` to a repetition without processing
  it again. `X-GruaLink-Delivery` is not covered by the signature and `id` in the body is: after
  verifying, check that both are equal and use that value.
- Answer the unsigned or badly signed with `401` and no detail about why.
- To order events, use `created_at` and the `status` inside `data.service_request`, or read the request.

## 11. Names shared by every language

Use the idiomatic casing of each language (`createServiceRequest`, `create_service_request`,
`CreateServiceRequest`) with these same words.

| Concept | Name |
|---|---|
| Client | `GrualinkClient` |
| Create | `createServiceRequest(input, { idempotencyKey })` → `{ serviceRequest, replayed, idempotencyKey }` |
| Read one | `getServiceRequest(id)` |
| Find | `findServiceRequestsByExternalRef(externalRef)` |
| Health | `health()` |
| Verify | `verifyWebhookSignature({ rawBody, signatureHeader, secret, nowSeconds, toleranceSeconds })` → `{ valid, reason }` |
| Sign (for local tests) | `computeWebhookSignature(secret, timestamp, rawBody)`, `buildSignatureHeader(secrets, timestamp, rawBody)` |
| Deduplication | `DeliveryDeduplicator` with `claim(deliveryId)` and `release(deliveryId)`; `InMemoryDeliveryDeduplicator` |
| Receiver | `createWebhookHandler({ secret, onEvent, deduplicator })` |

| Error | When |
|---|---|
| `GrualinkError` | Base of all |
| `GrualinkConfigError` | Invalid configuration or argument; nothing was sent |
| `GrualinkConnectionError` | No HTTP response (network) |
| `GrualinkTimeoutError` | No response within the timeout |
| `GrualinkApiError` | The API answered an error: `status`, `code`, `message`, `details`, `retryAfterSeconds`, `attempts` |
| `GrualinkInvalidRequestError` | 400, 405, 413, 415 |
| `GrualinkAuthenticationError` | 401 |
| `GrualinkPermissionError` | 403 |
| `GrualinkNotFoundError` | 404 |
| `GrualinkIdempotencyConflictError` | 409 |
| `GrualinkValidationError` | 422 |
| `GrualinkRateLimitError` | 429 |
| `GrualinkServerError` | 5xx |

An error answer whose body is not the documented shape uses the code `unexpected_response`.

Environment variables: `GRUALINK_API_KEY`, `GRUALINK_WEBHOOK_SECRET`, `GRUALINK_WEBHOOK_SECRET_PREVIOUS`
(optional), `GRUALINK_API_BASE_URL` (optional).

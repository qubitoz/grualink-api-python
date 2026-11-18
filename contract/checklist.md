# Checklist for every example repository

This folder (`contract/`) is copied **unchanged** into each language repository. It originates in the
Node.js repository, where `scripts/generate-signature-vectors.mjs` generates `signature-vectors.json`;
never edit the vectors by hand and never let the copies diverge.

Read `API.md` (or `API.es.md`) first. Then build a repository that meets every point below.

## Contents

- [ ] **Minimal client** (not a heavy SDK), with the names of `API.md` section 11:
  - [ ] create a service request with `Idempotency-Key`; returns the request, `replayed` and the key used
  - [ ] get by id; find by `external_ref`; health (sent without the key)
  - [ ] typed errors: one class per row of the error table in section 11, carrying `status`, `code`,
        `message`, `details`, `retryAfterSeconds`, `attempts`
  - [ ] retries only for network errors, timeouts, 5xx and `429 rate_limited`; exponential backoff with
        full jitter; `Retry-After` respected as a floor; `too_many_open_requests` not retried
  - [ ] the body is serialized once and the same bytes and the same `Idempotency-Key` are sent on every
        retry
  - [ ] a timeout on every attempt; redirects are not followed
  - [ ] the key never appears in logs, errors, exceptions or the printed form of the client, even when
        the server echoes it back
  - [ ] the base URL must be https (http only for localhost, for tests)
- [ ] **Webhook receiver**:
  - [ ] a pure verification function following the six steps of `API.md` section 10, in that order, with
        the same six reasons; it never throws
  - [ ] reads the RAW body (bytes) before any parsing; constant-time comparison; 300 s tolerance with an
        injectable clock; accepts any matching `v1` (rotation)
  - [ ] deduplicates by event id (`X-GruaLink-Delivery`, checked against `id` in the signed body); a
        repetition gets `2xx` without being processed again; a failed handler releases the id and
        answers `5xx` so the delivery is retried
  - [ ] answers `401` with no detail to a bad signature; caps the body it reads (64 KB)
  - [ ] an example server with the most common framework of the language, plus a note for a second one
- [ ] **Runnable end-to-end example** with a TEST key: requests a tow truck with fictitious data, prints
      the quote, repeats the call to show the replay, reads the status. It refuses to run with a live key.
- [ ] **Tests**:
  - [ ] every case of `signature-vectors.json`, verified against `body_base64` with the case's `now`
        (the test must fail if a case is skipped)
  - [ ] the client against a local fake server: success, each error code of the table, retries (429 with
        `Retry-After`, 5xx, network, timeout), idempotent replay (`200` + `Idempotent-Replayed: true`),
        and that the key appears in no error or log output
  - [ ] before trusting a green run, break the code on purpose at least twice (for example: skip the
        timestamp check; stop redacting the key) and confirm the tests fail; then restore
- [ ] **Documentation**: `README.md` in Spanish first and `README.en.md` in English: what it is,
      requirements, installation, environment variables by NAME, how to run the examples and the tests,
      the error table, and a link to <https://grualink.mx/api>.
- [ ] `LICENSE` (MIT, holder "GRUALINK", 2026), `.gitignore`, `.env.example` with names only, and CI on
      GitHub Actions (lint or type check, plus tests) on the supported versions of the language.

## Rules

- [ ] No secret and no key, not even an example with a realistic shape. Test values are obviously fake
      (`glk_test_FAKE_...`, `whsec_TEST_ONLY_FAKE_...`).
- [ ] Fictitious data only. No name of any real company or person.
- [ ] Only the public API is described. Nothing about how the platform is built: no providers, no
      storage, no internal names or addresses.
- [ ] Minimal, current dependencies; supported language versions declared.
- [ ] Code, identifiers and comments in English. Wire names (`external_ref`, `service_request`, error
      codes, reasons) exactly as in `API.md`; never translated.
- [ ] Plain conventional commit messages in English, with no generated footers. No editor or tool
      configuration folders in the repository.
- [ ] Never call the real API from the test suite. The suite runs offline.
- [ ] Before publishing, run the example for real with a test key of the test organization.

## Environment variables

| Name | Used by |
|---|---|
| `GRUALINK_API_KEY` | client and request example (test key) |
| `GRUALINK_WEBHOOK_SECRET` | webhook receiver |
| `GRUALINK_WEBHOOK_SECRET_PREVIOUS` | webhook receiver, optional, during a rotation |
| `GRUALINK_API_BASE_URL` | optional override of the base URL |

# Webhook Delivery Dashboard — Functional Spec

Living document. Built in small phases; each phase works end to end before the next starts.
Later phases are outlined only and are expanded when we reach them.

> **Review status:** only sections marked **Reviewed** are approved. Sections marked **Draft — needs review** are a first draft and may change before they are built.

| Phase | Scope | Spec | Build |
|---|---|---|---|
| 0 | Stack wiring: FastAPI + Postgres + tests + live deploy | Reviewed | ✓ Done (live) |
| 1 | Create event, duplicate handling, deliver once, view history | Reviewed | ☐ |
| 2 | Bounded retries + "process pending" | Draft — needs review | ☐ |
| 3 | HTTP destinations, timeouts, backoff, restart safety | Draft — needs review | ☐ |
| 4 | Browser UI, seed + reset, README | Draft — needs review | ☐ |

## Requirements coverage
| # | Requirement (from the brief) | Covered in |
|---|---|---|
| 1 | Create event with unique ID + JSON payload | Phase 1 |
| 2 | Select one of three simulated destinations | Phase 1 (in-process), Phase 3 (HTTP) |
| 3 | Attempt delivery; view status and history (timestamps, outcomes, errors) | Phase 1 |
| 4 | Bounded retries; button to process pending deliveries | Phase 2 |
| 5 | Duplicate submissions; defined behavior for same ID + different payload | Phase 1 |
| 6 | Retained across refresh and restart | Phase 0 (Postgres), Phase 3 (restart mid-delivery) |
| — | Delivery timeouts | Phase 3 |
| — | Browser UI, seed data, reset | Phase 4 |
| — | Tests: duplicates, retry limit, failed → successful | Phase 1 (T2, T4, T9), Phase 2 (retry limit) |

## Stack
- Python 3.13, FastAPI, SQLAlchemy 2.x, PostgreSQL, pytest
- DB: Neon Postgres (free) for both local dev and live; separate databases `webhooks` (app) and `webhooks_test` (tests). Host: Render (free).
- Config via env vars only (`DATABASE_URL`; tests use `TEST_DATABASE_URL`), loaded from `.env` locally with python-dotenv; same code locally and live.
- Tables created at startup (`create_all`). No migrations tool for now.

---

## Phase 0 — Wiring  ·  *Reviewed*

**Goal:** prove the stack works locally and live before writing features.

**Endpoints**
- `GET /` → `{"message": "Hello World"}`
- `GET /health` → reads from Postgres (`SELECT now()`) → `{"status": "ok", "db_time": "<timestamp>"}`; returns 503 if the DB is unreachable.

**Repo contents:** `app/`, `tests/`, `requirements.txt`, `.env.example`, `.gitignore`, `README.md`.

**Acceptance criteria**
- [x] `pytest` passes locally (test calls `/health`, expects 200).
- [x] `https://webhook-dashboard-2x9d.onrender.com/health` returns a timestamp read from Neon.
- [x] Push to `main` redeploys automatically.

---

## Phase 1 — Events, duplicates, single delivery  ·  *Reviewed*

### Destinations (simulated, in-process for this phase)
| Key | Behavior |
|---|---|
| `always_succeed` | Succeeds every time |
| `fail_twice` | Fails the 1st and 2nd attempt **for that event**, succeeds from the 3rd |
| `always_fail` | Fails every time (a retryable failure; only the retry limit in Phase 2 stops it) |

- The destination is chosen **when the event is created** and stored on the event, so later deliveries and retries know where to send.
- Implemented as Python functions behind one interface: `deliver(event) -> (ok: bool, error: str | None)`.
- `fail_twice` decides from the number of previous attempts for that event in the DB (not memory), so it survives restarts.

### Data model
**events**
| Column | Type | Rule |
|---|---|---|
| id | integer | primary key |
| event_id | text | **unique**, not null (client-supplied) |
| payload | jsonb | not null |
| destination | text | not null; one of the three keys |
| status | text | not null, default `PENDING`; `PENDING` → `SUCCEEDED` (`FAILED` added in Phase 2) |
| attempt_count | integer | not null, default 0 |
| created_at, updated_at | timestamptz | not null, UTC |

**delivery_attempts**
| Column | Type | Rule |
|---|---|---|
| id | integer | primary key |
| event_pk | integer | not null, references `events.id` |
| attempt_no | integer | not null |
| attempted_at | timestamptz | not null, UTC |
| outcome | text | not null; `SUCCESS` or `FAILURE` |
| error | text | nullable; empty on success |
| | | **unique (`event_pk`, `attempt_no`)** |

### API
**`POST /events`** — body `{ "event_id": str, "payload": object, "destination": str }`
| Case | Code | Body |
|---|---|---|
| New `event_id` | **201** | Event (`status: PENDING`, `attempt_count: 0`) |
| Same `event_id`, same payload and destination (any status) | **200** | Existing event with current status, `"duplicate": true`, `"message": "Event already submitted"`. Nothing changes. |
| Same `event_id`, different payload or destination (any status) | **422** | `"error": "event_id_reused_with_different_payload"` + message to use a new event_id. Original unchanged. Follows the IETF Idempotency-Key draft. |
| Different `event_id`, same payload | **201** | A separate event (the ID identifies an event, not its content) |
| Invalid input (missing field, bad JSON, payload not an object, unknown destination, empty id) | **422** | FastAPI's standard validation error (`"detail": [...]`) |

- Payloads are compared in the app as **canonical JSON** (`json.dumps(sort_keys=True, separators=(",", ":"))`), so key order and whitespace don't matter. Not `dict == dict`, because Python treats `True == 1`. `1` and `1.0` count as different payloads.
- Safe under concurrent duplicate submits: rely on the DB unique constraint (`INSERT … ON CONFLICT DO NOTHING`, then compare), never "check then insert". A simultaneous duplicate waits on the unique index, then sees the committed row.

**`POST /events/{event_id}/deliver`** — makes exactly one attempt now.
- Inserts the attempt row and updates the event (`attempt_count`, `updated_at`, `status`) **in one transaction**.

| Case | Code | Body |
|---|---|---|
| Attempt succeeded | **200** | Updated event (`SUCCEEDED`) + this attempt (`outcome: SUCCESS`) |
| Attempt failed | **200** | Updated event (`PENDING`) + this attempt (`outcome: FAILURE`, `error` set). The failure is data, not a server error. |
| Event already `SUCCEEDED` | **409** | `"error": "already_succeeded"`; no attempt made |
| Simultaneous deliver on the same event (the loser) | **409** | `"error": "delivery_conflict"` (unique `event_pk, attempt_no` blocks it) |
| Unknown event | **404** | `"error": "not_found"` |

**`GET /events`** — **200**: list (event_id, destination, status, attempt_count, created_at), newest first.

**`GET /events/{event_id}`** — **200**: event, payload and attempts ordered by attempt_no. Unknown → **404**.

### Acceptance criteria (automated tests)
| # | Test | Expected |
|---|---|---|
| T1 | Create a new event | 201, `PENDING`, 0 attempts |
| T2 | Same id + same payload twice | 2nd call 200, `duplicate: true`; 1 row in DB |
| T3 | Same id, keys in a different order | 200 (treated as identical) |
| T4 | Same id, different payload | 422 `event_id_reused_with_different_payload`; stored payload unchanged |
| T5 | Same id + payload, different destination | 422 |
| T6 | Different ids, same payload | 201, 201; two rows |
| T7 | Unknown destination | 422 (validation) |
| T8 | `always_succeed` → deliver | 200, `SUCCEEDED`, 1 attempt `SUCCESS` |
| T9 | `fail_twice` → deliver ×3 | FAILURE, FAILURE, SUCCESS; `SUCCEEDED`, attempt_count 3 |
| T10 | `always_fail` → deliver | 200, outcome FAILURE, error recorded, status `PENDING` |
| T11 | Deliver a `SUCCEEDED` event | 409; still 1 attempt |
| T12 | Deliver an unknown event | 404 |
| T13 | Duplicate submit after success | 200, `duplicate: true`, `status: SUCCEEDED` |
| T14 | `GET /events/{id}` after `fail_twice` ×3 | 3 attempts in order with attempted_at, outcome, error |

The simultaneous-deliver race is enforced by the DB unique rule and tested in Phase 3 with the delivery lock.

---

## Phase 2 — Bounded retries (outline)  ·  *Draft — needs review*
- Max attempts (default 5); after the last failure, status `FAILED` (terminal).
- `POST /deliveries/process` → one attempt for every eligible `PENDING` event.
- Tests: retry limit stops at max; fail → fail → success via process.

## Phase 3 — Resilience (outline)  ·  *Draft — needs review*
- Destinations become real HTTP endpoints (`POST /sim/{behavior}`), called with `httpx` and a timeout; event id sent as `Idempotency-Key` header; `fail_twice` keeps its own counter table.
- Backoff: `next_attempt_at` after each failure; process skips events not yet due.
- Restart safety: `IN_PROGRESS` + lease, rows claimed with `FOR UPDATE SKIP LOCKED`. Guarantee: at-least-once.
- Optional `slow` destination to demonstrate timeouts.

## Phase 4 — Ship (outline)  ·  *Draft — needs review*
- Single static HTML page: create form, events table, attempt history, "Process pending" and "Reset demo" buttons.
- Seed one event per destination on empty DB; `POST /admin/reset` wipes and reseeds.
- README: run locally, architecture, retry policy and guarantees, limitations, AI usage.

---

## Decisions & assumptions
| # | Decision | Why |
|---|---|---|
| D1 | FastAPI over .NET / Spring Boot | Fastest to build, free hosting is simple, built-in Swagger UI at `/docs` |
| D2 | Same id + different payload → 422, original kept (see D10) | Overwriting would break idempotency; client must use a new id |
| D3 | Different destination with same id + payload → 422 | The request is not identical |
| D4 | Payload compared as canonical JSON in the app; no hash column | Duplicates are found by event_id and payloads are small. At scale: store a SHA-256 of the canonical form |
| D5 | Creating an event does not deliver it | Keeps each step visible and testable |
| D6 | One destination per event | Matches the requirement; separate attempts table keeps multi-destination a small change |
| D7 | Phase 1 destinations are in-process functions | Avoids the app calling itself over HTTP before the basics work; replaced in Phase 3 |
| D8 | No authentication | Demo app; reviewers must not need credentials |
| D9 | Neon for local dev too (no Docker) | Docker not installed on the dev machine; one fewer moving part. Tests use a separate `webhooks_test` database |
| D10 | Same id + different payload → **422** in every status, with error code `event_id_reused_with_different_payload` | Follows the IETF Idempotency-Key draft (draft-ietf-httpapi-idempotency-key-header). Distinct error code separates it from FastAPI validation 422s. A duplicate submit never modifies data; edits would need an explicit PUT allowed only before the first attempt |
| D11 | Exact duplicate → **200** with current state in every status (incl. `PENDING`) | A retry must get a success response. IETF's 409 "still processing" applies only while the original request is in flight; create is a single atomic insert, so it can't occur here. PENDING is business state, not an in-flight request |
| D12 | A failed delivery attempt returns 200 | The API call worked; the destination's failure is data in the body |
| D13 | Destination chosen at create time | Retries need to know where to send. One event → many destinations would need a `deliveries` table (likely follow-up change) |
| D14 | All failures are retryable until the limit; no transient/permanent split | Keeps scope minimal; `always_fail` must be retried to demonstrate the limit. Production would treat 4xx (except 408/429) as permanent |

## AI usage log
Record each AI suggestion that was corrected or rejected (feeds the README section).

**Tools:** Claude (chat, in the desktop app) for planning, spec review and code review; Claude Code (VS Code extension) for implementation. Every Claude Code change started from a file plan I approved, and I reviewed every diff and ran the tests before committing.

**How I checked output:** compared each plan against `SPEC.md` before approving; read every diff; ran `pytest` locally and smoke-tested the live URL after each push; verified API design choices against primary sources (IETF Idempotency-Key draft, Stripe docs) rather than taking the AI's word.

| Date | Tool | Suggestion | What I did | Why |
|---|---|---|---|---|
| Nov 5 | Claude | First spec draft covered everything at once (multiple tables, leases, backoff, optional destinations) | Rejected; rescoped into small phases (0–4), each working end to end | Too much to review and build at once; phases keep each diff small and reviewable |
| Nov 5 | Claude | Local Postgres in Docker | Dropped; used Neon for local dev and live, separate test database | Docker not installed; one fewer moving part (D9) |
| Nov 5 | Claude Code | Phase 0 plan used Neon's `postgresql://` URL as-is | Required converting it to `postgresql+psycopg://` | SQLAlchemy reads the plain scheme as psycopg2, which isn't installed; the app would crash on startup |
| Nov 5 | Claude Code | `/health` 503 test planned with tables created at startup | Required simulating the DB failure *after* startup (monkeypatch), not an unreachable host | With startup DB init, the app never starts when the DB is down, so the 503 path could never be tested; a fake host can also hang the test |
| Nov 5 | Claude Code | Engine without connection health checks; unpinned dependencies; deprecated `on_event` startup | Added `pool_pre_ping=True`, pinned versions, used the lifespan handler | Neon suspends idle connections; reproducible Render builds; current FastAPI API |
| Nov 5 | Claude Code | Test setup failed with `ModuleNotFoundError: app` | Added `pytest.ini` with `pythonpath = .` | Plain `pytest` doesn't put the repo root on the import path |
| Nov 6 | Claude | Store a `payload_hash` column to detect duplicates | Dropped it; compare canonical JSON in the app (D4) | Duplicates are found by `event_id` and payloads are small, so a hash adds a column for no benefit. Also rejected plain `dict == dict` because Python treats `True == 1` |
| Nov 6 | Claude | Name the attempts foreign key `event_id` while pointing at `events.id` | Renamed to `event_pk` | Two different things named `event_id` would be confusing in code and in the walkthrough |
| Nov 6 | Me → Claude | I proposed choosing the destination at delivery time | Kept it at create time after discussing it (D13) | The "process pending" retry needs to know where to send; per-attempt destinations would need per-destination status. Kept as the answer to a "multiple destinations" follow-up |
| Nov 6 | Claude | Same ID + different payload → 409 | Changed to **422** with a distinct error code (D10) | Checked the IETF Idempotency-Key draft, which specifies 422 for key reuse with a different payload |
| Nov 6 | Me → Claude | I proposed 409 for an exact duplicate while the event is still `PENDING` | Kept **200** with current status after checking the IETF draft and Stripe docs (D11) | Their 409 is for a retry while the original *request* is still in flight; `PENDING` is business state, and a retry must get a success response |
| Nov 6 | Me → Claude | I proposed letting a pending event's payload be overwritten | Rejected (D10) | Attempts may already have sent the old payload; overwrites make retries unsafe (last write wins) |
| Nov 6 | Claude | Transient vs permanent failure classes | Out of scope (D14) | `always_fail` must be retried to demonstrate the limit; noted as a production change |
| Nov 6 | Claude Code | Offered a global handler mapping all `SQLAlchemyError` to 503 | Narrowed to `OperationalError` only; confirmed `IntegrityError` on deliver returns 409 `delivery_conflict` | 503 means "try later"; constraint violations are conflicts or bugs and shouldn't be masked as unavailability |
| Nov 6 | — | Router with `prefix="/events"` | Used `""` (not `"/"`) for collection routes | `"/"` makes the path `/events/` and causes a 307 redirect from `/events`, which can drop POST bodies |

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
Record of where I questioned, corrected or rejected AI output (feeds the README section).

**Tools:** Claude (chat, desktop app) for planning, spec discussion and reviewing Claude Code's plans; Claude Code (VS Code extension) for implementation.

**How I checked output:** every Claude Code change started from a file plan I approved against `SPEC.md`; I read every diff, ran `pytest` locally and smoke-tested the live URL after each push; I verified API design choices against primary sources (IETF Idempotency-Key draft, Stripe docs) instead of taking the AI's answer; I confirmed files actually changed on disk rather than trusting "done".

### Decisions I drove
| Date | AI suggested | What I did | Why | How I verified |
|---|---|---|---|---|
| Nov 5 | A full spec covering every requirement at once | rescoped into small phases (0–4), each working end to end | Smaller diffs I can actually review and explain | Phase 0 shipped live before any feature work |
| Nov 5 | A stack-only first phase was not planned | Added Phase 0: framework + DB + test + live deploy before features | Remove deployment risk first | `/health` read from Neon on the live URL |
| Nov 6 | A `payload_hash` column for duplicate detection | Questioned whether a hash was needed; dropped it and compare canonical JSON in the app (D4) | Duplicates are looked up by `event_id`, payloads are small; a hash added a column for no benefit | Tests T2–T4 (identical, reordered keys, different payload) |
| Nov 6 | A `finished_at` column on attempts | Dropped for Phase 1 | Delivery is an in-process call; one timestamp covers "timestamps". Added back only when restart detection needs it | Spec review |
| Nov 6 | Same ID + different payload → 409 | Changed to **422** with a distinct error code (D10) | Matches the IETF Idempotency-Key draft | Read the IETF draft and Stripe docs |
| Nov 6 | Exact duplicate → 200 | I challenged it for `PENDING` events (proposed 409); asked for it to be checked against standards; kept 200 (D11) | The standard's 409 is for a retry while the original *request* is still in flight; `PENDING` is business state | IETF draft + Stripe docs |
| Nov 6 | Destination stored on the event | I questioned why it's stored when the endpoints are simulated; kept it after walking through retries (D13) | "Process pending" must know where to send; one event → many destinations would need a separate table (kept as a follow-up answer) | Spec review |
| Nov 6 | (my idea) Overwrite payload while event is `PENDING` | Rejected after analysis (D10) | Attempts may already have sent the old payload; last-write-wins makes retries unsafe | Spec review |
| Nov 6 | (my idea) Transient vs permanent failure classes | Deferred to "production changes" (D14) | `always_fail` must be retried to demonstrate the retry limit | Spec review |
| Nov 6 | Claude Code offered to map all DB errors to 503 | Approved only a narrower handler: connection errors (`OperationalError`) → 503; confirmed constraint violations on deliver → 409 | 503 means "try later"; conflicts and bugs must not be masked as unavailability | Added a 503 test |
| Nov 6 | Routes defined with full paths | Moved event routes under `APIRouter(prefix="/events")`, using `""` not `"/"` for collection routes | Cleaner routing; `"/"` would cause a 307 redirect from `/events` | `pytest` passes unchanged |
| Nov 6 | AI reported spec updates as done | Noticed several times the file on disk had not changed; flagged it until fixed | Never trust "done" without checking the artifact | Checked the file content before each commit |

### Issues caught when Claude reviewed Claude Code's plans (I approved and applied each fix)
| Date | Claude Code's plan | Fix applied | Why | How I verified |
|---|---|---|---|---|
| Nov 5 | Used Neon's `postgresql://` URL as-is | Convert to `postgresql+psycopg://` | Plain scheme loads psycopg2 (not installed) → crash on startup | App starts locally and on Render |
| Nov 5 | 503 test with tables created at startup | Simulate DB failure *after* startup (monkeypatch) | Otherwise the app can't start when the DB is down, so 503 is untestable | 503 test passes |
| Nov 5 | No connection health check; unpinned deps; deprecated `on_event` | `pool_pre_ping=True`, pinned versions, lifespan handler | Neon suspends idle connections; reproducible builds | Live `/health` after idle period |
| Nov 5 | `pytest` failed: `ModuleNotFoundError: app` | Added `pytest.ini` (`pythonpath = .`) | Repo root wasn't on the import path | `pytest` passes |

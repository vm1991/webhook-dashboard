# Webhook Delivery Dashboard — Functional Spec

Living document. Built in small phases; each phase works end to end before the next starts.
Later phases are outlined only and are expanded when we reach them.

| Phase | Scope | Status |
|---|---|---|
| 0 | Stack wiring: FastAPI + Postgres + tests + live deploy | ☐ |
| 1 | Create event, duplicate handling, deliver once, view history | ☐ |
| 2 | Bounded retries + "process pending" | ☐ |
| 3 | HTTP destinations, timeouts, backoff, restart safety | ☐ |
| 4 | Browser UI, seed + reset, README | ☐ |

## Stack
- Python 3.13, FastAPI, SQLAlchemy 2.x, PostgreSQL, pytest
- DB: Neon Postgres (free) for both local dev and live; separate databases `webhooks` (app) and `webhooks_test` (tests). Host: Render (free).
- Config via env vars only (`DATABASE_URL`; tests use `TEST_DATABASE_URL`), loaded from `.env` locally with python-dotenv; same code locally and live.
- Tables created at startup (`create_all`). No migrations tool for now.

---

## Phase 0 — Wiring

**Goal:** prove the stack works locally and live before writing features.

**Endpoints**
- `GET /` → `{"message": "Hello World"}`
- `GET /health` → reads from Postgres (`SELECT now()`) → `{"status": "ok", "db_time": "<timestamp>"}`; returns 503 if the DB is unreachable.

**Repo contents:** `app/`, `tests/`, `requirements.txt`, `.env.example`, `.gitignore`, `README.md`.

**Acceptance criteria**
- [ ] `pytest` passes locally (test calls `/health`, expects 200).
- [ ] `https://<app>.onrender.com/health` returns a timestamp read from Neon.
- [ ] Push to `main` redeploys automatically.

---

## Phase 1 — Events, duplicates, single delivery

### Destinations (simulated, in-process for this phase)
| Key | Behavior |
|---|---|
| `always_succeed` | Succeeds every time |
| `fail_twice` | Fails the 1st and 2nd attempt **for that event**, succeeds from the 3rd |
| `always_fail` | Fails every time |

- Implemented as Python functions behind one interface: `deliver(event) -> (ok: bool, error: str | None)`.
- `fail_twice` decides from the number of previous attempts for that event stored in the DB (not in memory), so it survives restarts.

### Data model
**events**
| Column | Type | Notes |
|---|---|---|
| id | serial PK | |
| event_id | text, **unique** | client-supplied |
| payload | jsonb | |
| payload_hash | text | SHA-256 of canonical JSON |
| destination | text | one of the three keys |
| status | text | `PENDING` or `SUCCEEDED` (more in Phase 2) |
| attempt_count | int | default 0 |
| created_at, updated_at | timestamptz | |

**delivery_attempts**
| Column | Type | Notes |
|---|---|---|
| id | serial PK | |
| event_id | FK → events | |
| attempt_no | int | 1, 2, 3… |
| started_at, finished_at | timestamptz | |
| outcome | text | `SUCCESS` or `FAILURE` |
| error | text, nullable | failure reason |

### API
**`POST /events`** — body `{ "event_id": str, "payload": object, "destination": str }`
| Case | Response |
|---|---|
| New `event_id` | **201** + event |
| Same `event_id`, same payload and destination | **200** + existing event; nothing created |
| Same `event_id`, different payload or destination | **409** `{ "error": "event_id already exists with a different payload" }`; original unchanged |
| Invalid input (bad JSON, unknown destination, empty id) | **422** |

- "Same payload" = same **canonical JSON** (sorted keys, no whitespace): `{"a":1,"b":2}` equals `{"b":2,"a":1}`.
- Must be safe under concurrent duplicate submits: rely on the DB unique constraint (`INSERT … ON CONFLICT DO NOTHING`, then compare hashes), never "check then insert".

**`POST /events/{event_id}/deliver`** — makes exactly one attempt now.
- Records an attempt row, increments `attempt_count`.
- Success → status `SUCCEEDED`. Failure → status stays `PENDING` (can be delivered again).
- Already `SUCCEEDED` → **409**, no attempt made.
- Unknown event → **404**.

**`GET /events`** — list: event_id, destination, status, attempt_count, created_at (newest first).

**`GET /events/{event_id}`** — event incl. payload + attempts ordered by attempt_no.

### Acceptance criteria (automated tests)
- [ ] Same id + same payload twice → 1 row; second call returns 200.
- [ ] Same id, keys in a different order → treated as the same (200).
- [ ] Same id, different payload → 409; stored payload unchanged.
- [ ] `always_succeed` → one deliver → `SUCCEEDED`, 1 attempt.
- [ ] `fail_twice` → deliver ×3 → attempts FAILURE, FAILURE, SUCCESS; status `SUCCEEDED`.
- [ ] `always_fail` → deliver → `PENDING`, attempt has an error message.
- [ ] Deliver on a `SUCCEEDED` event → 409, no new attempt.

---

## Phase 2 — Bounded retries (outline)
- Max attempts (default 5); after the last failure, status `FAILED` (terminal).
- `POST /deliveries/process` → one attempt for every eligible `PENDING` event.
- Tests: retry limit stops at max; fail → fail → success via process.

## Phase 3 — Resilience (outline)
- Destinations become real HTTP endpoints (`POST /sim/{behavior}`), called with `httpx` and a timeout; event id sent as `Idempotency-Key` header; `fail_twice` keeps its own counter table.
- Backoff: `next_attempt_at` after each failure; process skips events not yet due.
- Restart safety: `IN_PROGRESS` + lease, rows claimed with `FOR UPDATE SKIP LOCKED`. Guarantee: at-least-once.
- Optional `slow` destination to demonstrate timeouts.

## Phase 4 — Ship (outline)
- Single static HTML page: create form, events table, attempt history, "Process pending" and "Reset demo" buttons.
- Seed one event per destination on empty DB; `POST /admin/reset` wipes and reseeds.
- README: run locally, architecture, retry policy and guarantees, limitations, AI usage.

---

## Decisions & assumptions
| # | Decision | Why |
|---|---|---|
| D1 | FastAPI over .NET / Spring Boot | Fastest to build, free hosting is simple, built-in Swagger UI at `/docs` |
| D2 | Same id + different payload → 409, original kept | Overwriting would break idempotency; client must use a new id |
| D3 | Different destination with same id + payload → 409 | The request is not identical |
| D4 | Payload compared as canonical JSON hash | Key order / whitespace shouldn't create false conflicts |
| D5 | Creating an event does not deliver it | Keeps each step visible and testable |
| D6 | One destination per event | Matches the requirement; separate attempts table keeps multi-destination a small change |
| D7 | Phase 1 destinations are in-process functions | Avoids the app calling itself over HTTP before the basics work; replaced in Phase 3 |
| D8 | No authentication | Demo app; reviewers must not need credentials |
| D9 | Neon for local dev too (no Docker) | Docker not installed on the dev machine; one fewer moving part. Tests use a separate `webhooks_test` database |

## AI usage log
Record each AI suggestion that was corrected or rejected (feeds the README section).

| Date | Suggestion | What I did | Why |
|---|---|---|---|
| | | | |

# Webhook Delivery Dashboard — Functional Spec

Living document. Built in small phases; each phase works end to end before the next starts.
Later phases are outlined only and are expanded when we reach them.

> **Review status:** only sections marked **Reviewed** are approved. Sections marked **Draft — needs review** are a first draft and may change before they are built.

| Phase | Scope | Spec | Build |
|---|---|---|---|
| 0 | Stack wiring: FastAPI + Postgres + tests + live deploy | Reviewed | ✓ Done (live) |
| 1 | Create event, duplicate handling, deliver once, view history | Reviewed | ✓ Done (live) |
| 2 | Bounded retries + "process pending" | Reviewed | ✓ Done (live) |
| 3 | HTTP destinations, timeouts, restart safety | Reviewed | ✓ Done (live) |
| 4 | Browser UI, seed + reset, README | Reviewed | ✓ Done (live) |

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
| — | Tests: duplicates, retry limit, failed → successful | Phase 1 (T2, T4, T9), Phase 2 (T15, T17, T18) |

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
| Invalid input (missing field, bad JSON, payload not an object, unknown destination, empty id, id with characters other than letters, digits, `_`, `-`) | **422** | FastAPI's standard validation error (`"detail": [...]`) |

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

**`GET /events`** — **200**: list (event_id, destination, payload, status, attempt_count, created_at), newest first. (`payload` added in Phase 4, D30.)

**`GET /events/{event_id}`** — **200**: event, payload and attempts ordered by attempt_no. Unknown → **404**.

### Acceptance criteria (automated tests)
| # | Test | Expected |
|---|---|---|
| T1 | Create a new event | 201, `PENDING`, 0 attempts |
| T2 | Same id + same payload twice | 2nd call 200, `duplicate: true`; 1 row in DB |
| T3 | Same id, keys in a different order | 200 (treated as identical) |
| T4 | Same id, different payload | 422 `event_id_reused_with_different_payload`; stored payload unchanged |
| T4.2 | Same id, payload `{"a": true}` then `{"a": 1}` | 422 (canonical JSON tells `true` from `1`) |
| T5 | Same id + payload, different destination | 422 |
| T6 | Different ids, same payload | 201, 201; two rows |
| T7 | Unknown destination | 422 (validation) |
| T7.2 | `event_id` containing `/` | 422 (validation) |
| T8 | `always_succeed` → deliver | 200, `SUCCEEDED`, 1 attempt `SUCCESS` |
| T9 | `fail_twice` → deliver ×3 | FAILURE, FAILURE, SUCCESS; `SUCCEEDED`, attempt_count 3 |
| T10 | `always_fail` → deliver | 200, outcome FAILURE, error recorded, status `PENDING` |
| T11 | Deliver a `SUCCEEDED` event | 409; still 1 attempt |
| T12 | Deliver an unknown event | 404 |
| T13 | Duplicate submit after success | 200, `duplicate: true`, `status: SUCCEEDED` |
| T14 | `GET /events/{id}` after `fail_twice` ×3 | 3 attempts in order with attempted_at, outcome, error |

The simultaneous-deliver race is enforced by the DB unique rule; it has no automated test (needs real concurrency). See D23 for the production approach.

---

## Phase 2 — Bounded retries + process pending  ·  *Reviewed*

### Config
- `MAX_ATTEMPTS` env var: **total** attempts per event (1 first try + retries). Default `5`, must be an integer ≥ 1 (invalid → error). Read at delivery time, so tests can override it. Listed in `.env.example`.

### Rules
- A failed attempt with `attempt_no >= MAX_ATTEMPTS` sets status `FAILED` (terminal), in the same transaction as the attempt. `>=` (not `==`) so lowering the limit later doesn't leave events stuck in `PENDING`.
- Statuses: `PENDING` → `SUCCEEDED` | `FAILED`. No change to `events` (status is text).
- Duplicate submit (`POST /events`, same id + payload) of a `FAILED` event → 200, `duplicate: true`, `status: FAILED` (D11). Resubmitting is not a replay.

### Data model (new table)
**process_lock** — at most one row; a lease so only one Process run happens at a time (D21)
| Column | Type | Rule |
|---|---|---|
| id | integer | primary key; always `1` |
| owner | text | not null; random token of the run holding the lease |
| locked_until | timestamptz | not null; lease expiry (DB clock) |

- New table, so `create_all` creates it on startup; existing tables are untouched (no reset needed).
- **Acquire** (one atomic statement): `INSERT INTO process_lock (id, owner, locked_until) VALUES (1, :owner, now() + interval '60 seconds') ON CONFLICT (id) DO UPDATE SET owner = EXCLUDED.owner, locked_until = EXCLUDED.locked_until WHERE process_lock.locked_until < now()`. 1 row affected → acquired; 0 → another run holds it.
- **Release** in `finally`: `UPDATE process_lock SET locked_until = now() WHERE id = 1 AND owner = :owner` (owner check: an expired slow run can't release a newer run's lease).
- Crash mid-round → lease expires after 60 s; Process works again.

### API changes
**`POST /events/{event_id}/deliver`** — new case:
| Case | Code | Body |
|---|---|---|
| Event already `FAILED` | **409** | `"error": "already_failed"`; no attempt made |

**`POST /deliveries/process`** — one retry round (backs the "Process pending" button).
- Acquires the `process_lock` lease first; released when the round ends (success or error).
- Every `PENDING` event, oldest first (`created_at`, `id`), gets **one attempt**. One round per call, no loop.
- Uses the same attempt logic as `/deliver` (one shared helper). **Each event in its own transaction**, so one failure doesn't undo the others.
- If an event hits a concurrent-delivery conflict with `/deliver` (`IntegrityError`), that event is rolled back, not counted, and the round continues.

| Case | Code | Body |
|---|---|---|
| Round ran | **200** | `{"processed", "succeeded", "failed", "still_pending"}` |
| Another run holds the lease | **409** | `"error": "process_already_running"`; no attempts made |

- `succeeded`: moved to `SUCCEEDED` this round
- `failed`: reached the limit, moved to `FAILED` this round
- `still_pending`: attempt failed, still under the limit
- `processed` = succeeded + failed + still_pending

### Acceptance criteria (automated tests)
| # | Test | Expected |
|---|---|---|
| T15 | `always_fail` → deliver ×5 | Attempts 1–4 → `PENDING`; 5th → `FAILED`, attempt_count 5 |
| T16 | Deliver a `FAILED` event | 409 `already_failed`; still 5 attempts |
| T17 | One event per destination → process ×3 | Round 1: processed 3, succeeded 1, still_pending 2 (`always_succeed` → `SUCCEEDED`). Round 2: processed 2, still_pending 2 (`always_succeed` not picked). Round 3: processed 2, succeeded 1, still_pending 1 (`fail_twice` → `SUCCEEDED` on attempt 3; `always_fail` `PENDING`, 3 attempts) |
| T18 | `always_fail` → process ×6 | Rounds 1–4: still_pending 1. Round 5: failed 1 → `FAILED`. Round 6: processed 0; still 5 attempts |
| T19 | Process with nothing pending | 200, all counts 0 |
| T20 | Duplicate submit of a `FAILED` event | 200, `duplicate: true`, `status: FAILED` |
| T21 | `MAX_ATTEMPTS=2`, `always_fail` → deliver ×2 | `FAILED` after 2 attempts |
| T22 | Lock row held (`locked_until` in the future), then process | 409 `process_already_running`; no new attempts |
| T23 | Lock row expired (`locked_until` in the past), then process | 200, round runs (recovery after a crash) |

T17/T18 call Process several times in a row, which also proves the lease is released after each round.

## Phase 3 — Timeouts + restart safety  ·  *Reviewed*

Brief: "Resilience patterns, including bounded retries and delivery timeouts." Bounded retries = Phase 2; this phase adds timeouts. Backoff is not built (D24).

### Destinations become one HTTP route in the same app: `POST /sim/{name}`
The sim route plays the **receiver** (the customer's webhook URL); `/deliver` and Process are the **sender**. It lives in the same app only so there is one thing to deploy (D22).

| `name` | Behavior |
|---|---|
| `always_succeed` | 200 |
| `fail_twice` | 500 when `X-Attempt-No` ≤ 2, otherwise 200 (D25) |
| `always_fail` | 500 |
| `always_timeout` (**new**; first named `slow`, D29) | Waits `SIM_SLOW_SECONDS` (default 5), then 200 — longer than the timeout, so it always times out |

- One handler, `async def`, waits with `asyncio.sleep` so a slow receiver doesn't block one of the worker threads that sync endpoints run on.
- `always_timeout` is added to the allowed destinations in `EventCreate`. No table change: `events.destination` stays text and now names the sim route to call.

### Delivery call (inside `attempt_delivery`, replaces the in-process function call)
- `POST {SIM_BASE_URL}/sim/{destination}` with httpx: payload as JSON body; headers `Idempotency-Key: <event_id>` and `X-Attempt-No: <attempt_no>`; timeout `DELIVERY_TIMEOUT_SECONDS` (default 2).
- `SIM_BASE_URL` defaults to `http://127.0.0.1:$PORT` (`PORT` defaults to 8000 locally; Render sets it). This is the only setting that points at the receiver.
- Attempt numbering is unchanged: `attempt_no = attempt_count + 1` from the DB, stored in `delivery_attempts`. The header only passes that number to the receiver.

| Result | Outcome | `error` |
|---|---|---|
| 2xx | SUCCESS | — |
| Non-2xx | FAILURE | `HTTP <code>` |
| `httpx.TimeoutException` | FAILURE | `timeout after <n>s` |
| Other `httpx.RequestError` | FAILURE | `request error: <type>` |

- All failures count toward `MAX_ATTEMPTS` (D14).
- New env vars `SIM_BASE_URL`, `DELIVERY_TIMEOUT_SECONDS`, `SIM_SLOW_SECONDS` listed in `.env.example`, all optional.

### Restart safety (no new code)
- The attempt and the event update commit **after** the call returns, in one transaction (unchanged). A crash mid-call commits nothing: the event stays `PENDING` with no attempt row and is retried next round. There is no `IN_PROGRESS` state, so nothing can get stuck. A crash mid-round leaves `process_lock`, which expires within 60 s (D21).
- Guarantee: **at-least-once**. The receiver may get the same event twice; `Idempotency-Key` lets it deduplicate (D23).

### Acceptance criteria (automated tests)
| # | Test | Expected |
|---|---|---|
| — | All Phase 1–2 tests | Pass unchanged; in tests the sim calls go through `TestClient` (an httpx client) |
| T24 | `always_fail` → deliver | FAILURE, error `HTTP 500` |
| T25 | Delivery call raises `httpx.TimeoutException` (faked; no real 5 s wait) | FAILURE, error `timeout after 2s`, status `PENDING` |
| T26 | Deliver any event | Request carries `Idempotency-Key` = event_id and `X-Attempt-No` = 1 |
| T27 | `fail_twice` → deliver ×3 through `/sim` | FAILURE, FAILURE, SUCCESS |
| T28 | Create an event with `destination: always_timeout` | 201 (accepted) |
| T29 | `SIM_SLOW_SECONDS=0`, then `POST /sim/always_timeout` directly | 200 (route exists and answers after its wait) |

No automated end-to-end timeout test: `TestClient` runs the app in-process, and httpx timeouts are enforced by its network transport, so an `always_timeout` call in tests would wait and return 200. T25 (timeout → FAILURE), T28 and T29 cover the pieces; the live check covers them together.

Manual check after deploy: create an `always_timeout` event on the live URL, deliver it, see `timeout after 2s` in its history.

## Phase 4 — UI, seed + reset  ·  *Reviewed* (README is the final step, written separately)

### Browser UI: `GET /`
- One static file, `app/static/index.html`: plain HTML plus a little vanilla JS calling the existing JSON API. No framework, no styling. First pass, kept simple (D26).
- `GET /` serves this page instead of "Hello World", so `test_root` gets updated.

| Section | What it does |
|---|---|
| Create event | Fields: `event_id` (prefilled with a fresh `evt-<random>`), payload as a JSON text area (default `{"order_id": 1001, "quantity": 2}`), destination drop-down with all 4. Submit → `POST /events`; shows the status code and the response. |
| Deliver | `event_id` input plus a **Deliver** button → `POST /events/{event_id}/deliver`; shows the status code and the response. |
| Event history | `event_id` input plus a **Get history** button → `GET /events/{event_id}`; shows the event and its attempts (attempt_no, time, outcome, error). |
| Process pending | Button → `POST /deliveries/process`; shows the counts. Disabled while its request runs; the lock still covers other tabs. |
| Reset demo | Button → `POST /admin/reset`. |
| Event status legend | Static box at the top right: what `PENDING`, `SUCCEEDED` and `FAILED` mean, and that a failed attempt (outcome `FAILURE`) doesn't make the event `FAILED` (D31). |
| Events table | `GET /events`: event_id, destination, status, attempts, created, **payload** (wrapped JSON). Each row has a **History** button: shows that event's attempts (attempt_no, time, outcome, error) in a row directly under it, or "No attempts yet"; click again to close. Open rows stay open and are re-fetched after every refresh (D30). |

The table refreshes after every action. Error responses (404/409/422) are shown as they come back, with their status code.

### Seed + reset
- **Seed:** at startup, if `events` is empty, create one event per destination (`seed-always-succeed`, `seed-fail-twice`, `seed-always-fail`, `seed-always-timeout`), all `PENDING`, each with payload `{"order_id": 1001, "quantity": 2}`. Uses `INSERT … ON CONFLICT DO NOTHING`, so a restart or a second instance can't duplicate them (D27).
- **`POST /admin/reset`:** in one transaction, `TRUNCATE delivery_attempts, events, process_lock` (**without** `RESTART IDENTITY`), then seeds again. Returns 200 `{"reset": true, "seeded": 4}`. No auth (D8): anyone with the URL can reset the demo.
- A reset during a Process round is safe: TRUNCATE waits for the delivery transaction in progress, and events that disappear mid-round are skipped (`session.get` returns `None`). Ids are not restarted, so an in-flight round holding old ids can never pick up the newly seeded events by reused ids.

### Acceptance criteria (automated tests)
| # | Test | Expected |
|---|---|---|
| T30 | `GET /` | 200, HTML, contains the "Process pending" button |
| T31 | Make some attempts, then reset | 200; exactly 4 seeded events, all `PENDING` with 0 attempts |
| T32 | Run seed twice on an empty DB | Still 4 events |
| T33 | Lock held, then reset, then process | Process returns 200 (reset cleared the lock) |
| T34 | `GET /events` after creating an event | Each item includes the stored `payload` |

The page's JavaScript has no automated test; it is checked by hand locally and on the live URL in an incognito window.

### README
Written last, reviewed by me as a first-time reader would. Structure: live link and cold start; a 2-minute walkthrough; a requirements checklist mapped to the brief; statuses explained once (attempt outcome vs event status) with Mermaid diagrams, including the `fail_twice` flow; assumptions; a page guide with every response in a collapsible table; then the brief's five sections (run and test locally, architecture and data model, retry policy and guarantees, limitations and production changes, AI usage).

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
| D7 | Phase 1 destinations are in-process functions | Avoids the app calling itself over HTTP before the basics work; replaced in Phase 3 (D22) |
| D8 | No authentication | Demo app; reviewers must not need credentials |
| D9 | Neon for local dev too (no Docker) | Docker not installed on the dev machine; one fewer moving part. Tests use a separate `webhooks_test` database |
| D10 | Same id + different payload → **422** in every status, with error code `event_id_reused_with_different_payload` | Follows the IETF Idempotency-Key draft (draft-ietf-httpapi-idempotency-key-header). Distinct error code separates it from FastAPI validation 422s. A duplicate submit never modifies data; edits would need an explicit PUT allowed only before the first attempt |
| D11 | Exact duplicate → **200** with current state in every status (incl. `PENDING`) | A retry must get a success response. IETF's 409 "still processing" applies only while the original request is in flight; create is a single atomic insert, so it can't occur here. PENDING is business state, not an in-flight request |
| D12 | A failed delivery attempt returns 200 | The API call worked; the destination's failure is data in the body |
| D13 | Destination chosen at create time | Retries need to know where to send. One event → many destinations would need a `deliveries` table (likely follow-up change) |
| D14 | All failures are retryable until the limit; no transient/permanent split | Keeps scope minimal; `always_fail` must be retried to demonstrate the limit. Production would treat 4xx (except 408/429) as permanent. Retryability belongs to the attempt's error (e.g. a `retryable` flag on attempts), not to event status |
| D15 | DB connection errors (`OperationalError`) → **503** `{"error": "database_unavailable"}`, no details in the body | 503 means "try again later". Constraint violations (`IntegrityError`) are not caught here: on deliver they become 409, anything else stays 500 so bugs aren't hidden |
| D16 | `event_id` limited to letters, digits, `_`, `-` | The id is used in URL paths: `/` would make the event unreachable (404), `.`/`..` get rewritten by clients |
| D17 | `status` and `outcome` typed as `Literal` in the output models | Server-owned values are still constrained: a typo fails loudly, and `/docs` shows the allowed values. Production: also a DB `CHECK` constraint (needs table recreate, since `create_all` doesn't alter tables) |
| D18 | `MAX_ATTEMPTS` = total attempts, from env, default 5 | It's the number you see in the history (`attempt_no`); changeable without a code change |
| D19 | `FAILED` is terminal; `/deliver` on it → 409 `already_failed` | Otherwise the limit isn't a limit. Retrying a failed event would be an explicit `POST /events/{id}/replay` (resets to `PENDING`, keeps history) — a follow-up, not built |
| D20 | Process = one round, one transaction per event, summary counts only | The UI only needs counts and reloads the table; per-event transactions isolate failures; a Process vs `/deliver` conflict on one event is skipped, not counted |
| D21 | Only one Process run at a time, enforced by a lease row (`process_lock`, 60 s, owner token, DB clock); second caller → 409 `process_already_running` | Two browsers can click at once, so a UI-only fix isn't enough. A lease (not a flag) recovers by itself after a crash. Advisory locks rejected: Neon's pooled connections don't hold session locks reliably. Phase 3: TTL must exceed the longest round (events × timeout), or renew per event. Production with `SKIP LOCKED` workers may allow parallel runs instead |
| D22 | Destinations are one HTTP route in the same app (`POST /sim/{name}`), called with httpx and a timeout (replaces D7) | A real timeout needs a real network call; a Python function can't be stopped from outside. Same deploy, no extra service; `SIM_BASE_URL` is the only thing that points at the receiver. Production: the event references an `endpoints` table (customer URL, secret) |
| D23 | Commit after the call; no `IN_PROGRESS` state; at-least-once delivery with `Idempotency-Key` | A crash can't leave a stuck event. Trade-off: the DB transaction (and a pooled connection) stays open during the call, up to the timeout. Production: claim with a lease + `FOR UPDATE SKIP LOCKED`, call outside the transaction, background worker instead of a button |
| D24 | Backoff (`next_attempt_at`) not built | Needs a new column (`create_all` can't alter tables) and changes the Process round behavior; not in the brief. README: exponential backoff with jitter |
| D25 | `fail_twice` decides from the `X-Attempt-No` header | The receiver can't see the sender's DB; a receiver-side counter table would add a table that reset must clear. Source of truth for attempts stays `delivery_attempts`; after a crash the retry is sent as the same attempt number again |
| D26 | One static HTML page with vanilla JS calling the JSON API; Deliver and Event history take an `event_id` input (plus per-row History, D30) | Simplest UI that covers every requirement; no build step; the API stays the single source of behavior |
| D27 | Seed when empty at startup, and on reset; reset is public | A reviewer always lands on data, even after a Render restart. No auth is acceptable for a demo (D8) |
| D28 | Reset does not use `RESTART IDENTITY` (the test fixture still does) | A Process round in flight holds a list of event ids; restarted ids would let it deliver the new seed events. Fresh ids make stale ids return `None`, so they are skipped |
| D29 | Destination `slow` renamed to `always_timeout` | "slow" suggested it eventually succeeds; the name now says what the user sees and matches `always_succeed` / `always_fail`. `SIM_SLOW_SECONDS` kept |
| D30 | Per-row History and a payload column in the events table; `GET /events` returns `payload` | Found by testing as a user: attempts and payload are expected next to the event, not only via a separate lookup |
| D31 | Status legend on the page | Found by testing as a user: `PENDING` after a `FAILURE` attempt looked like a bug. Static text (shows the default of 5 attempts) |

## AI usage log
Full dated record behind the README's *AI usage* summary: where I set the direction, questioned, corrected or rejected AI output, and how I verified each result.

**Tools:** Claude (chat, desktop app) for planning, spec discussion and reviewing Claude Code's plans; Claude Code (VS Code extension) for implementation.

**How I worked:** I set the phases and the scope of each one, reviewed every spec section point by point before any code, and made the design calls; the AI drafted options and code.

**How I checked output:** every Claude Code change started from a file plan I approved against `SPEC.md`; I read every diff, ran `pytest` locally and smoke-tested the live URL after each push; I verified API design choices against primary sources (IETF Idempotency-Key draft, Stripe docs) instead of taking the AI's answer; I confirmed files actually changed on disk rather than trusting "done".

### Decisions I drove
| Date | AI suggested | What I did | Why | How I verified |
|---|---|---|---|---|
| Nov 5 | A full spec covering every requirement at once | I didn't build from it; I split the work into small phases (0–4), each working end to end | Smaller diffs I can actually review and explain | Phase 0 shipped live before any feature work |
| Nov 5 | No deploy-first phase in the plan | I added Phase 0: framework + DB + test + live deploy before any feature | Remove deployment risk first | `/health` read from Neon on the live URL |
| Nov 6 | A `payload_hash` column for duplicate detection | I questioned whether a hash was needed, dropped it, and compare canonical JSON in the app instead (D4) | Duplicates are looked up by `event_id`, payloads are small; a hash added a column for no benefit | Tests T2–T4 (identical, reordered keys, different payload) |
| Nov 6 | A `finished_at` column on attempts | I dropped it | Delivery is an in-process call; one timestamp covers "timestamps". Added back only when restart detection needs it | Spec review |
| Nov 6 | Same ID + different payload → 409 | I checked the IETF draft and Stripe docs and changed it to **422** with a distinct error code (D10) | Matches the IETF Idempotency-Key draft | Read the IETF draft and Stripe docs |
| Nov 6 | Exact duplicate → 200 | I challenged it for `PENDING` events (proposed 409) and checked it against the standard before accepting; kept 200 (D11) | The standard's 409 is for a retry while the original *request* is still in flight; `PENDING` is business state | IETF draft + Stripe docs |
| Nov 6 | Destination stored on the event | I questioned why it's stored when the endpoints are simulated; kept it after walking through retries (D13) | "Process pending" must know where to send; one event → many destinations would need a separate table (kept as a follow-up answer) | Spec review |
| Nov 6 | (my idea) Overwrite payload while event is `PENDING` | Rejected after analysis (D10) | Attempts may already have sent the old payload; last-write-wins makes retries unsafe | Spec review |
| Nov 6 | (my idea) Transient vs permanent failure classes | Deferred to "production changes" (D14) | `always_fail` must be retried to demonstrate the retry limit | Spec review |
| Nov 6 | Claude Code offered to map all DB errors to 503 | I approved only a narrower handler: connection errors (`OperationalError`) → 503; constraint violations on deliver stay 409 | 503 means "try later"; conflicts and bugs must not be masked as unavailability | Added a 503 test |
| Nov 6 | Routes defined with full paths | I had event routes moved under `APIRouter(prefix="/events")`, using `""` not `"/"` for collection routes | Cleaner routing; `"/"` would cause a 307 redirect from `/events` | `pytest` passes unchanged |
| Nov 6 | AI reported spec updates as done | I noticed several times the file on disk had not changed and flagged it until fixed | Never trust "done" without checking the artifact | Checked the file content before each commit |
| Nov 6 | `status`/`outcome` as plain `str` in output schemas | During my code walkthrough I asked why `status` had no `Literal`; added `Literal` types to three fields by hand (D17) | Server-owned values should still be constrained; catches typos, documents the enum | `pytest` passes |
| Nov 6 | Hard-coded `MAX_ATTEMPTS = 5` | I made it an env var (D18) | Limit may change per environment without a code change | T21 |
| Nov 6 | A `skipped` count in the process response | I questioned it and dropped it (D20) | Untestable in Phase 2, and always 0 with `SKIP LOCKED` workers (production design) | Spec review |
| Nov 6 | (my idea) `FAILED_TRANSIENT` / `FAILED_PERMANENT` statuses | I deferred it after analysis: retryability belongs to the attempt's error, not the event's status (D14) | A transient failure under the limit is just `PENDING`; no simulated destination returns a permanent error | Spec review |
| Nov 6 | No lock on Process (unique constraint keeps data correct; disable the button in the UI) | I rejected it and required a server-side lock: two browsers can click at once, and a disabled button only protects one tab. I chose a lease row over a flag (a crash would leave it stuck) and over an advisory lock (unreliable with Neon's pooled connections) (D21) | A double run gives events extra early attempts and inconsistent counts | T22, T23 |
| Nov 6 | Possibly cutting Phase 3 timeouts to save time | I re-read the brief ("Resilience patterns, including bounded retries and delivery timeouts"), kept timeouts, and cut backoff and the `IN_PROGRESS`/lease code instead (D23, D24) | Timeouts are an explicit requirement; backoff is not | Re-read the brief |
| Nov 6 | Adding `response_model` to every route (after I noticed only `list_events` had one) | I questioned whether it was needed for routes with several outcomes and decided to skip it | Those routes return several status codes and bodies; a `response_model` would document only one success shape and isn't applied when a `JSONResponse` is returned | Code review |
| Nov 6 | Only a faked timeout test (T25) for the timeout destination | I asked for direct coverage: T28/T29 added. I evaluated a real end-to-end timeout test (uvicorn in a test thread) and deferred it on purpose; documented in the README | The timeout path should be covered by more than one fake | T28, T29; live check |
| Nov 7 | — (my own test of the finished app as a first-time user) | I renamed `slow` → `always_timeout` (D29), added per-row History and a payload column (D30) and a status legend on the page (D31), and had the README explain that a failed attempt doesn't fail the event | Names and statuses were confusing for a new user | Manual test on the live app; T34 |
| Nov 7 | First README draft: long, repetitive, technical | I reviewed it as the hiring manager would and had it restructured: walkthrough first, requirements checklist mapped to the brief, the brief's five sections, wording that matches the page ("event ID", "attempt", "payload") | The README is what reviewers read first | Re-read as a reviewer; walkthrough steps re-checked on the live app |

### Issues caught in plan review (Claude reviewing Claude Code's plans; I approved, applied and verified each fix)
| Date | Claude Code's plan | Fix applied | Why | How I verified |
|---|---|---|---|---|
| Nov 5 | Used Neon's `postgresql://` URL as-is | Convert to `postgresql+psycopg://` | Plain scheme loads psycopg2 (not installed) → crash on startup | App starts locally and on Render |
| Nov 5 | 503 test with tables created at startup | Simulate DB failure *after* startup (monkeypatch) | Otherwise the app can't start when the DB is down, so 503 is untestable | 503 test passes |
| Nov 5 | No connection health check; unpinned deps; deprecated `on_event` | `pool_pre_ping=True`, pinned versions, lifespan handler | Neon suspends idle connections; reproducible builds | Live `/health` after idle period |
| Nov 5 | `pytest` failed: `ModuleNotFoundError: app` | Added `pytest.ini` (`pythonpath = .`) | Repo root wasn't on the import path | `pytest` passes |
| Nov 6 | `event_id` accepted any non-empty string | Flagged during my code walkthrough: an id with `/` can be created but never fetched. I added the pattern and test T7.2 by hand (D16) | Path parameters don't match `/` | T7.2 passes |
| Nov 6 | No test for the `True == 1` case that motivated canonical JSON | I wrote T4.2 by hand | The test should prove the reason for the design | T4.2 passes |
| Nov 6 | Process loaded the PENDING ids, then attempted each event without checking its status again | Load each event in its own session and skip it unless still `PENDING` | A concurrent `/deliver` could finish the event in between, and Process would then attempt an already `SUCCEEDED` event | Read the full Phase 2 diff myself and confirmed the check; `pytest` passes |
| Nov 6 | Lock acquire and release not specified as separate transactions | Acquire commits immediately in its own session; release uses a fresh session in `finally` | Otherwise a second caller blocks on the row lock instead of getting 409, and a failed round could fail to release | Read the diff myself; T22, T23 pass |
| Nov 6 | Phase 3 plan didn't specify the order of the except blocks for the HTTP call | Catch `httpx.TimeoutException` before `httpx.RequestError` | `TimeoutException` is a subclass of `RequestError`; the wrong order records every timeout as `request error: ReadTimeout` | Read the diff myself; T25 passes; live `slow` (now `always_timeout`) event shows `timeout after 2s` |
| Nov 6 | Reset used `TRUNCATE … RESTART IDENTITY` | Reset without `RESTART IDENTITY` (D28) | An in-flight Process round holds old event ids; reused ids would let it deliver the new seed events | Read the diff; T31, T33 |
| Nov 6 | Page plan didn't fix how responses are rendered or where the HTML is loaded from | `textContent` / `createElement` only, never `innerHTML`; path relative to the code file | Public demo with user-supplied payloads; the app may start from another folder | Read the diff (no `innerHTML`); live page works |

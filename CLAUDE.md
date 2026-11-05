# CLAUDE.md

Take-home assignment: Webhook Delivery Dashboard. The full spec is in `SPEC.md`; it is the source of truth.

## How to work
- Build only the scope I name: a whole phase, or one or more specific line items from a phase (e.g. "Phase 1: POST /events and its duplicate tests"). Don't build beyond that scope, start the next phase, or add features from later phases.
- Before writing code, list the files you will create or change and why, and wait for my OK.
- Minimal diffs. No refactors or extra files outside the phase.
- Every behavior in a phase's acceptance criteria gets a pytest test.
- Ask before adding a dependency that isn't in the stack below.
- If something in `SPEC.md` is unclear or seems wrong, say so instead of guessing.

## Stack
- Python 3.13, FastAPI, SQLAlchemy 2.x, psycopg 3, PostgreSQL (Neon), pytest, python-dotenv, httpx (test client now; delivery HTTP calls from Phase 3).
- Config only via environment variables loaded from `.env` (`DATABASE_URL`, `TEST_DATABASE_URL`). Never hard-code credentials.
- Tables created at startup with `create_all`; no migrations tool.

## Environment
- Windows, PowerShell, VS Code. Use a virtual environment in `.venv`.
- No Docker. Local dev and live both use Neon; tests use a separate database (`webhooks_test`).
- Deployed on Render (free tier) from GitHub `main`.

## Never
- Commit `.env` or any connection string.
- Use in-memory state for anything that must survive a restart.
- Use "check then insert" for duplicates; rely on the DB unique constraint.

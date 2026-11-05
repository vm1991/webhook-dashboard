# Webhook Delivery Dashboard

See `SPEC.md` for the full spec.

## Run locally (Windows, PowerShell)
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # then fill in DATABASE_URL and TEST_DATABASE_URL
uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000/health (Swagger UI at `/docs`).

## Tests
```powershell
pytest
```
Tests use `TEST_DATABASE_URL` (the `webhooks_test` database).

## Deploy (Render, free tier)
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Env vars: `DATABASE_URL` (Neon `webhooks` database), `PYTHON_VERSION=3.13.7`
- Auto-deploys on push to `main`.

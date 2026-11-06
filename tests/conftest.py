import os

from dotenv import load_dotenv

load_dotenv()

# Must happen before the app is imported: tests run against webhooks_test.
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from sqlalchemy import text  # noqa: E402

from app import db, destinations  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("SIM_BASE_URL", "http://testserver")
    with TestClient(app) as c:
        # Route the sender's HTTP call through the in-process app.
        monkeypatch.setattr(
            destinations,
            "_send",
            lambda url, payload, headers, timeout: c.post(url, json=payload, headers=headers),
        )
        # Tables exist after startup; start every test from an empty DB.
        with db.engine.begin() as conn:
            conn.execute(
                text("TRUNCATE delivery_attempts, events, process_lock RESTART IDENTITY")
            )
        yield c

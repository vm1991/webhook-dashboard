from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app import db, seed

SEED_IDS = {"seed-always-succeed", "seed-fail-twice", "seed-always-fail", "seed-always-timeout"}


def _make_event(client, event_id, destination="always_fail"):
    r = client.post(
        "/events",
        json={"event_id": event_id, "payload": {"a": 1}, "destination": destination},
    )
    assert r.status_code == 201


def test_root_serves_ui(client):  # T30
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Process pending" in r.text


def test_reset_leaves_four_pending_seed_events(client):  # T31
    _make_event(client, "mine-1", "always_fail")
    _make_event(client, "mine-2", "always_succeed")
    client.post("/events/mine-1/deliver")
    client.post("/events/mine-2/deliver")

    r = client.post("/admin/reset")
    assert r.status_code == 200
    assert r.json() == {"reset": True, "seeded": 4}

    events = client.get("/events").json()
    assert {e["event_id"] for e in events} == SEED_IDS
    assert all(e["status"] == "PENDING" and e["attempt_count"] == 0 for e in events)
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM delivery_attempts")).scalar_one() == 0


def test_seed_twice_still_four_events(client):  # T32
    for _ in range(2):
        with db.SessionLocal() as session:
            seed.seed_demo(session)
            session.commit()
    assert len(client.get("/events").json()) == 4


def test_reset_clears_process_lock(client):  # T33
    future = datetime.now(timezone.utc) + timedelta(minutes=5)
    with db.engine.begin() as conn:
        conn.execute(
            text("INSERT INTO process_lock (id, owner, locked_until) VALUES (1, 'x', :t)"),
            {"t": future},
        )
    assert client.post("/deliveries/process").status_code == 409

    assert client.post("/admin/reset").status_code == 200
    assert client.post("/deliveries/process").status_code == 200

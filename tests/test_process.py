import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import db
from app.config import get_max_attempts
from app.main import app


def create(client, destination, event_id="evt-1", payload=None):
    r = client.post(
        "/events",
        json={
            "event_id": event_id,
            "payload": payload or {"k": "v"},
            "destination": destination,
        },
    )
    assert r.status_code == 201


def process(client):
    r = client.post("/deliveries/process")
    assert r.status_code == 200
    return r.json()


def get(client, event_id="evt-1"):
    return client.get(f"/events/{event_id}").json()


def set_lock(interval_sql):
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO process_lock (id, owner, locked_until) "
                f"VALUES (1, 'other-run', now() + interval '{interval_sql}')"
            )
        )


def test_t15_always_fail_hits_limit(client):
    create(client, "always_fail")
    for _ in range(4):
        r = client.post("/events/evt-1/deliver")
        assert r.json()["event"]["status"] == "PENDING"
    r = client.post("/events/evt-1/deliver")
    assert r.status_code == 200
    assert r.json()["event"]["status"] == "FAILED"
    assert r.json()["event"]["attempt_count"] == 5


def test_t16_deliver_failed_event(client):
    create(client, "always_fail")
    for _ in range(5):
        client.post("/events/evt-1/deliver")
    r = client.post("/events/evt-1/deliver")
    assert r.status_code == 409
    assert r.json()["error"] == "already_failed"
    assert len(get(client)["attempts"]) == 5


def test_t17_process_rounds_per_destination(client):
    create(client, "always_succeed", "a")
    create(client, "fail_twice", "b")
    create(client, "always_fail", "c")

    assert process(client) == {
        "processed": 3, "succeeded": 1, "failed": 0, "still_pending": 2,
    }
    assert get(client, "a")["status"] == "SUCCEEDED"

    assert process(client) == {
        "processed": 2, "succeeded": 0, "failed": 0, "still_pending": 2,
    }

    assert process(client) == {
        "processed": 2, "succeeded": 1, "failed": 0, "still_pending": 1,
    }
    assert get(client, "b")["status"] == "SUCCEEDED"
    c = get(client, "c")
    assert c["status"] == "PENDING"
    assert c["attempt_count"] == 3


def test_t18_process_until_failed(client):
    create(client, "always_fail")
    for _ in range(4):
        assert process(client)["still_pending"] == 1
    assert process(client) == {
        "processed": 1, "succeeded": 0, "failed": 1, "still_pending": 0,
    }
    assert get(client)["status"] == "FAILED"
    assert process(client)["processed"] == 0
    assert get(client)["attempt_count"] == 5


def test_t19_nothing_pending(client):
    assert process(client) == {
        "processed": 0, "succeeded": 0, "failed": 0, "still_pending": 0,
    }


def test_t20_duplicate_of_failed_event(client):
    create(client, "always_fail")
    for _ in range(5):
        client.post("/events/evt-1/deliver")
    r = client.post(
        "/events",
        json={"event_id": "evt-1", "payload": {"k": "v"}, "destination": "always_fail"},
    )
    assert r.status_code == 200
    assert r.json()["duplicate"] is True
    assert r.json()["status"] == "FAILED"


def test_t21_max_attempts_override(client, monkeypatch):
    monkeypatch.setenv("MAX_ATTEMPTS", "2")
    create(client, "always_fail")
    assert client.post("/events/evt-1/deliver").json()["event"]["status"] == "PENDING"
    assert client.post("/events/evt-1/deliver").json()["event"]["status"] == "FAILED"


def test_t22_lock_held(client):
    create(client, "always_fail")
    set_lock("5 minutes")
    r = client.post("/deliveries/process")
    assert r.status_code == 409
    assert r.json()["error"] == "process_already_running"
    event = get(client)
    assert event["attempt_count"] == 0
    assert event["attempts"] == []


def test_t23_lock_expired(client):
    create(client, "always_fail")
    set_lock("-5 minutes")
    assert process(client)["processed"] == 1
    assert get(client)["attempt_count"] == 1


@pytest.mark.parametrize("bad", ["0", "-1", "abc", "2.5", ""])
def test_invalid_max_attempts_raises(monkeypatch, bad):
    monkeypatch.setenv("MAX_ATTEMPTS", bad)
    with pytest.raises(RuntimeError):
        get_max_attempts()


def test_invalid_max_attempts_fails_startup(monkeypatch):
    monkeypatch.setenv("MAX_ATTEMPTS", "abc")
    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass

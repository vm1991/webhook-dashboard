from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app import db
from app.models import Event


def body(event_id="evt-1", payload=None, destination="always_succeed"):
    return {
        "event_id": event_id,
        "payload": {"a": 1, "b": 2} if payload is None else payload,
        "destination": destination,
    }


def count_events():
    with db.SessionLocal() as s:
        return s.scalar(select(func.count()).select_from(Event))


def stored_payload(event_id):
    with db.SessionLocal() as s:
        return s.scalar(select(Event.payload).where(Event.event_id == event_id))


def test_t1_create_new_event(client):
    r = client.post("/events", json=body())
    assert r.status_code == 201
    data = r.json()
    assert data["status"] == "PENDING"
    assert data["attempt_count"] == 0
    assert data["event_id"] == "evt-1"


def test_t2_same_id_same_payload_is_duplicate(client):
    client.post("/events", json=body())
    r = client.post("/events", json=body())
    assert r.status_code == 200
    assert r.json()["duplicate"] is True
    assert r.json()["message"] == "Event already submitted"
    assert count_events() == 1


def test_t3_key_order_does_not_matter(client):
    client.post("/events", json=body(payload={"a": 1, "b": 2}))
    r = client.post("/events", json=body(payload={"b": 2, "a": 1}))
    assert r.status_code == 200
    assert r.json()["duplicate"] is True


def test_t4_1_same_id_different_payload(client):
    client.post("/events", json=body(payload={"a": 1}))
    r = client.post("/events", json=body(payload={"a": 2}))
    assert r.status_code == 422
    assert r.json()["error"] == "event_id_reused_with_different_payload"
    assert stored_payload("evt-1") == {"a": 1}


def test_t4_2_true_and_1_are_different_payloads(client):
    client.post("/events", json=body(payload={"a": True}))
    r = client.post("/events", json=body(payload={"a": 1}))
    assert r.status_code == 422


def test_t5_same_id_payload_different_destination(client):
    client.post("/events", json=body(destination="always_succeed"))
    r = client.post("/events", json=body(destination="always_fail"))
    assert r.status_code == 422
    assert r.json()["error"] == "event_id_reused_with_different_payload"


def test_t6_different_ids_same_payload(client):
    assert client.post("/events", json=body("e1")).status_code == 201
    assert client.post("/events", json=body("e2")).status_code == 201
    assert count_events() == 2


def test_t7_unknown_destination(client):
    r = client.post("/events", json=body(destination="nope"))
    assert r.status_code == 422
    assert "detail" in r.json()
    

def test_t7_2_event_id_with_slash_rejected(client):
    r = client.post("/events", json=body(event_id="a/b"))
    assert r.status_code == 422
    assert "detail" in r.json()


def test_t13_duplicate_after_success(client):
    client.post("/events", json=body())
    assert client.post("/events/evt-1/deliver").status_code == 200
    r = client.post("/events", json=body())
    assert r.status_code == 200
    assert r.json()["duplicate"] is True
    assert r.json()["status"] == "SUCCEEDED"


def test_database_unavailable_returns_503(client, monkeypatch):
    def boom():
        raise OperationalError("SELECT 1", {}, Exception("secret connection detail"))

    monkeypatch.setattr(db, "SessionLocal", boom)
    r = client.post("/events", json=body())
    assert r.status_code == 503
    assert r.json() == {"error": "database_unavailable"}


def test_list_events_includes_payload(client):
    payload = {"order_id": 1001, "items": [1, 2], "nested": {"a": True}}
    client.post("/events", json=body("p-1", payload))
    rows = client.get("/events").json()
    assert rows[0]["payload"] == payload

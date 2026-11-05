from sqlalchemy.exc import OperationalError

from app import db


def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.json() == {"message": "Hello World"}


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db_time"]


def test_health_503_when_db_fails_after_startup(client, monkeypatch):
    # `client` has already started the app (lifespan ran); now break the DB.
    def broken_session():
        raise OperationalError("SELECT now()", {}, Exception("connection lost"))

    monkeypatch.setattr(db, "SessionLocal", broken_session)
    r = client.get("/health")
    assert r.status_code == 503

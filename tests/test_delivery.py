def create(client, destination, event_id="evt-1"):
    r = client.post(
        "/events",
        json={"event_id": event_id, "payload": {"k": "v"}, "destination": destination},
    )
    assert r.status_code == 201


def test_t8_always_succeed(client):
    create(client, "always_succeed")
    r = client.post("/events/evt-1/deliver")
    assert r.status_code == 200
    data = r.json()
    assert data["event"]["status"] == "SUCCEEDED"
    assert data["event"]["attempt_count"] == 1
    assert data["attempt"]["outcome"] == "SUCCESS"


def test_t9_fail_twice(client):
    create(client, "fail_twice")
    results = [client.post("/events/evt-1/deliver").json() for _ in range(3)]
    assert [x["attempt"]["outcome"] for x in results] == [
        "FAILURE",
        "FAILURE",
        "SUCCESS",
    ]
    assert results[-1]["event"]["status"] == "SUCCEEDED"
    assert results[-1]["event"]["attempt_count"] == 3


def test_t10_always_fail(client):
    create(client, "always_fail")
    r = client.post("/events/evt-1/deliver")
    assert r.status_code == 200
    data = r.json()
    assert data["attempt"]["outcome"] == "FAILURE"
    assert data["attempt"]["error"]
    assert data["event"]["status"] == "PENDING"


def test_t11_deliver_succeeded_event(client):
    create(client, "always_succeed")
    client.post("/events/evt-1/deliver")
    r = client.post("/events/evt-1/deliver")
    assert r.status_code == 409
    assert r.json()["error"] == "already_succeeded"
    detail = client.get("/events/evt-1").json()
    assert len(detail["attempts"]) == 1


def test_t12_deliver_unknown_event(client):
    r = client.post("/events/missing/deliver")
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"


def test_t14_history_after_fail_twice(client):
    create(client, "fail_twice")
    for _ in range(3):
        client.post("/events/evt-1/deliver")
    detail = client.get("/events/evt-1").json()
    attempts = detail["attempts"]
    assert [a["attempt_no"] for a in attempts] == [1, 2, 3]
    assert [a["outcome"] for a in attempts] == ["FAILURE", "FAILURE", "SUCCESS"]
    assert attempts[0]["error"] and attempts[1]["error"]
    assert attempts[2]["error"] is None
    assert all(a["attempted_at"] for a in attempts)
    assert detail["payload"] == {"k": "v"}


def test_get_unknown_event_and_list_order(client):
    assert client.get("/events/missing").status_code == 404
    create(client, "always_succeed", "first")
    create(client, "always_succeed", "second")
    ids = [e["event_id"] for e in client.get("/events").json()]
    assert ids == ["second", "first"]

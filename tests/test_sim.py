import httpx

from app import destinations


def create(client, destination, event_id="evt-1"):
    return client.post(
        "/events",
        json={"event_id": event_id, "payload": {"k": "v"}, "destination": destination},
    )


def test_t24_always_fail_http_500(client):
    create(client, "always_fail")
    attempt = client.post("/events/evt-1/deliver").json()["attempt"]
    assert attempt["outcome"] == "FAILURE"
    assert attempt["error"] == "HTTP 500"


def test_t25_timeout_is_failure(client, monkeypatch):
    def raise_timeout(url, payload, headers, timeout):
        raise httpx.TimeoutException("boom")

    monkeypatch.setattr(destinations, "_send", raise_timeout)
    create(client, "always_succeed")
    data = client.post("/events/evt-1/deliver").json()
    assert data["attempt"]["outcome"] == "FAILURE"
    assert data["attempt"]["error"] == "timeout after 2s"
    assert data["event"]["status"] == "PENDING"


def test_t26_headers_sent(client, monkeypatch):
    seen = {}
    real_send = destinations._send

    def spy(url, payload, headers, timeout):
        seen.update(headers)
        return real_send(url, payload, headers, timeout)

    monkeypatch.setattr(destinations, "_send", spy)
    create(client, "always_succeed")
    client.post("/events/evt-1/deliver")
    assert seen["Idempotency-Key"] == "evt-1"
    assert seen["X-Attempt-No"] == "1"


def test_t27_fail_twice_through_sim(client):
    create(client, "fail_twice")
    outcomes = [
        client.post("/events/evt-1/deliver").json()["attempt"]["outcome"]
        for _ in range(3)
    ]
    assert outcomes == ["FAILURE", "FAILURE", "SUCCESS"]


def test_t28_slow_destination_accepted(client):
    assert create(client, "slow").status_code == 201


def test_t29_sim_slow_route_answers(client, monkeypatch):
    monkeypatch.setenv("SIM_SLOW_SECONDS", "0")
    assert client.post("/sim/slow").status_code == 200

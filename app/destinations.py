"""Delivery call: POST to the simulated receiver (/sim/{destination}) with httpx."""
import httpx

from app.config import get_delivery_timeout, get_sim_base_url


def _send(url: str, payload: dict, headers: dict, timeout: float) -> httpx.Response:
    # Single seam for the HTTP call; tests replace it to go through TestClient.
    return httpx.post(url, json=payload, headers=headers, timeout=timeout)


def deliver(event, attempt_no: int) -> tuple[bool, str | None]:
    timeout = get_delivery_timeout()
    url = f"{get_sim_base_url()}/sim/{event.destination}"
    headers = {
        "Idempotency-Key": event.event_id,
        "X-Attempt-No": str(attempt_no),
    }
    try:
        response = _send(url, event.payload, headers, timeout)
    except httpx.TimeoutException:  # before RequestError: it is a subclass
        return False, f"timeout after {timeout:g}s"
    except httpx.RequestError as exc:
        return False, f"request error: {type(exc).__name__}"
    if response.is_success:
        return True, None
    return False, f"HTTP {response.status_code}"

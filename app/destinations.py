"""Simulated in-process destinations (Phase 1)."""


def always_succeed(event) -> tuple[bool, str | None]:
    return True, None


def always_fail(event) -> tuple[bool, str | None]:
    return False, "always_fail: destination rejected the delivery"


def fail_twice(event) -> tuple[bool, str | None]:
    # attempt_count is the DB value loaded for this request (previous attempts),
    # so the behavior survives restarts.
    if event.attempt_count < 2:
        return False, f"fail_twice: simulated failure on attempt {event.attempt_count + 1}"
    return True, None


_DESTINATIONS = {
    "always_succeed": always_succeed,
    "fail_twice": fail_twice,
    "always_fail": always_fail,
}


def deliver(event) -> tuple[bool, str | None]:
    return _DESTINATIONS[event.destination](event)

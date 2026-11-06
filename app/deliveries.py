"""Delivery attempts (shared by /deliver and Process) and the Process round."""
import uuid

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import db, destinations
from app.config import get_max_attempts
from app.models import DeliveryAttempt, Event, utcnow
from app.schemas import ProcessSummary

router = APIRouter(prefix="/deliveries")

_ACQUIRE = text(
    "INSERT INTO process_lock (id, owner, locked_until) "
    "VALUES (1, :owner, now() + interval '60 seconds') "
    "ON CONFLICT (id) DO UPDATE "
    "SET owner = EXCLUDED.owner, locked_until = EXCLUDED.locked_until "
    "WHERE process_lock.locked_until < now()"
)
_RELEASE = text(
    "UPDATE process_lock SET locked_until = now() WHERE id = 1 AND owner = :owner"
)


def attempt_delivery(session: Session, event: Event) -> DeliveryAttempt | None:
    """Make one attempt and commit attempt + event update in one transaction.

    Returns None if a concurrent delivery won (unique event_pk/attempt_no).
    """
    attempt_no = event.attempt_count + 1
    ok, err = destinations.deliver(event, attempt_no)
    now = utcnow()

    attempt = DeliveryAttempt(
        event_pk=event.id,
        attempt_no=attempt_no,
        attempted_at=now,
        outcome="SUCCESS" if ok else "FAILURE",
        error=None if ok else err,
    )
    session.add(attempt)
    event.attempt_count = attempt_no
    event.updated_at = now
    if ok:
        event.status = "SUCCEEDED"
    elif attempt_no >= get_max_attempts():
        event.status = "FAILED"

    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return None
    session.refresh(event)
    return attempt


def acquire_lock(owner: str) -> bool:
    # Own session, committed immediately: a second caller sees rowcount 0.
    with db.SessionLocal() as session:
        result = session.execute(_ACQUIRE, {"owner": owner})
        session.commit()
        return result.rowcount == 1


def release_lock(owner: str) -> None:
    with db.SessionLocal() as session:
        session.execute(_RELEASE, {"owner": owner})
        session.commit()


def process_round() -> ProcessSummary:
    with db.SessionLocal() as session:
        ids = session.scalars(
            select(Event.id)
            .where(Event.status == "PENDING")
            .order_by(Event.created_at, Event.id)
        ).all()

    succeeded = failed = still_pending = 0
    for event_pk in ids:
        with db.SessionLocal() as session:
            event = session.get(Event, event_pk)
            # A concurrent /deliver may have finished it since the id list was read.
            if event is None or event.status != "PENDING":
                continue
            if attempt_delivery(session, event) is None:
                continue  # conflict with /deliver: not counted
            if event.status == "SUCCEEDED":
                succeeded += 1
            elif event.status == "FAILED":
                failed += 1
            else:
                still_pending += 1

    return ProcessSummary(
        processed=succeeded + failed + still_pending,
        succeeded=succeeded,
        failed=failed,
        still_pending=still_pending,
    )


@router.post("/process", response_model=ProcessSummary)
def process():
    owner = uuid.uuid4().hex
    if not acquire_lock(owner):
        return JSONResponse(
            status_code=409,
            content={
                "error": "process_already_running",
                "message": "Another Process run is in progress",
            },
        )
    try:
        return process_round()
    finally:
        release_lock(owner)

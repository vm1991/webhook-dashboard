"""Demo seed data and the public reset endpoint (D27)."""
from fastapi import APIRouter
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import db
from app.models import Event, utcnow

router = APIRouter(prefix="/admin")

SEED_PAYLOAD = {"order_id": 1001, "quantity": 2}
SEED_EVENTS = [
    ("seed-always-succeed", "always_succeed"),
    ("seed-fail-twice", "fail_twice"),
    ("seed-always-fail", "always_fail"),
    ("seed-slow", "slow"),
]


def seed_demo(session: Session) -> int:
    """Insert the seed events; ON CONFLICT DO NOTHING so repeats can't duplicate. No commit."""
    now = utcnow()
    rows = [
        {
            "event_id": event_id,
            "payload": SEED_PAYLOAD,
            "destination": destination,
            "status": "PENDING",
            "attempt_count": 0,
            "created_at": now,
            "updated_at": now,
        }
        for event_id, destination in SEED_EVENTS
    ]
    inserted = session.execute(
        insert(Event)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["event_id"])
        .returning(Event.id)
    ).all()
    return len(inserted)


def seed_if_empty() -> None:
    with db.SessionLocal() as session:
        if session.scalar(select(Event.id).limit(1)) is None:
            seed_demo(session)
            session.commit()


@router.post("/reset")
def reset():
    with db.SessionLocal() as session:
        # No RESTART IDENTITY: a Process round in flight holds old event ids; if ids were
        # reused by the new seed rows, that round would deliver them. Stale ids stay gone.
        session.execute(text("TRUNCATE delivery_attempts, events, process_lock"))
        seeded = seed_demo(session)
        session.commit()
    return {"reset": True, "seeded": seeded}

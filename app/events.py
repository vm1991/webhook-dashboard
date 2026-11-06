import json
from typing import Any

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from app import db, destinations
from app.models import DeliveryAttempt, Event, utcnow
from app.schemas import (
    AttemptOut,
    DeliverResult,
    EventCreate,
    EventCreated,
    EventDetail,
    EventListItem,
    EventOut,
)

router = APIRouter()


def canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "message": message})


def respond(status: int, model) -> JSONResponse:
    return JSONResponse(status_code=status, content=jsonable_encoder(model))


@router.post("/events")
def create_event(body: EventCreate):
    with db.SessionLocal() as session:
        # The DB unique constraint decides who wins; never "check then insert".
        inserted_id = session.execute(
            insert(Event)
            .values(
                event_id=body.event_id,
                payload=body.payload,
                destination=body.destination,
                status="PENDING",
                attempt_count=0,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            .on_conflict_do_nothing(index_elements=["event_id"])
            .returning(Event.id)
        ).scalar_one_or_none()
        session.commit()

        event = session.scalar(select(Event).where(Event.event_id == body.event_id))

        if inserted_id is not None:
            return respond(201, EventCreated.model_validate(event))

        if (
            canonical(event.payload) == canonical(body.payload)
            and event.destination == body.destination
        ):
            out = EventCreated.model_validate(event)
            out.duplicate = True
            out.message = "Event already submitted"
            return respond(200, out)

        return error(
            422,
            "event_id_reused_with_different_payload",
            "This event_id was already used with a different payload or destination. "
            "Use a new event_id.",
        )


@router.post("/events/{event_id}/deliver")
def deliver_event(event_id: str):
    with db.SessionLocal() as session:
        event = session.scalar(select(Event).where(Event.event_id == event_id))
        if event is None:
            return error(404, "not_found", "Event not found")
        if event.status == "SUCCEEDED":
            return error(409, "already_succeeded", "Event was already delivered")

        attempt_no = event.attempt_count + 1
        ok, err = destinations.deliver(event)
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

        try:
            session.commit()  # attempt INSERT + event UPDATE in one transaction
        except IntegrityError:
            session.rollback()
            return error(
                409, "delivery_conflict", "Another delivery for this event is in progress"
            )

        session.refresh(event)
        return respond(
            200,
            DeliverResult(
                event=EventOut.model_validate(event),
                attempt=AttemptOut.model_validate(attempt),
            ),
        )


@router.get("/events", response_model=list[EventListItem])
def list_events():
    with db.SessionLocal() as session:
        rows = session.scalars(
            select(Event).order_by(Event.created_at.desc(), Event.id.desc())
        ).all()
        return [EventListItem.model_validate(r) for r in rows]


@router.get("/events/{event_id}")
def get_event(event_id: str):
    with db.SessionLocal() as session:
        event = session.scalar(select(Event).where(Event.event_id == event_id))
        if event is None:
            return error(404, "not_found", "Event not found")
        attempts = session.scalars(
            select(DeliveryAttempt)
            .where(DeliveryAttempt.event_pk == event.id)
            .order_by(DeliveryAttempt.attempt_no)
        ).all()
        detail = EventDetail(
            **EventOut.model_validate(event).model_dump(),
            attempts=[AttemptOut.model_validate(a) for a in attempts],
        )
        return respond(200, detail)

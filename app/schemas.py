from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class EventCreate(BaseModel):
    event_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    payload: dict[str, Any]
    destination: Literal["always_succeed", "fail_twice", "always_fail", "always_timeout"]


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: str
    payload: dict[str, Any]
    destination: str
    status: Literal["PENDING", "SUCCEEDED", "FAILED"]
    attempt_count: int
    created_at: datetime
    updated_at: datetime


class EventCreated(EventOut):
    duplicate: bool = False
    message: str | None = None


class EventListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: str
    destination: str
    payload: dict[str, Any]
    status: Literal["PENDING", "SUCCEEDED", "FAILED"]
    attempt_count: int
    created_at: datetime


class AttemptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    attempt_no: int
    attempted_at: datetime
    outcome:  Literal["SUCCESS", "FAILURE"]
    error: str | None


class EventDetail(EventOut):
    attempts: list[AttemptOut]


class ProcessSummary(BaseModel):
    processed: int
    succeeded: int
    failed: int
    still_pending: int


class DeliverResult(BaseModel):
    event: EventOut
    attempt: AttemptOut

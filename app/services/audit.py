import json

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditEvent, User


def client_ip(request: Request | None) -> str:
    if request is None:
        return ""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


def record_event(
    db: Session,
    event_type: str,
    *,
    request: Request | None = None,
    actor: User | None = None,
    target: User | None = None,
    outcome: str = "success",
    detail: dict | None = None,
) -> AuditEvent:
    event = AuditEvent(
        event_type=event_type,
        actor_user_id=actor.id if actor else None,
        target_user_id=target.id if target else None,
        outcome=outcome,
        request_id=getattr(request.state, "request_id", "") if request else "",
        source_ip=client_ip(request),
        detail=json.dumps(detail or {}, separators=(",", ":"), sort_keys=True),
    )
    db.add(event)
    return event

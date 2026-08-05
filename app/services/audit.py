from __future__ import annotations

import json

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AuditEvent, User
from app.security.client import client_ip as resolve_client_ip

AUDIT_PAGE_SIZE = 50


def client_ip(request: Request | None) -> str:
    return resolve_client_ip(request, get_settings())


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


def list_audit_events(
    db: Session, *, event_type: str = "", outcome: str = "", page: int = 1
) -> tuple[list[AuditEvent], int, int, str, str]:
    """Return a page of audit events matching optional type/outcome filters."""
    page = max(1, page)
    statement = select(AuditEvent)
    count_statement = select(func.count()).select_from(AuditEvent)
    conditions = []
    et = event_type.strip()[:100]
    oc = outcome.strip()[:40]
    if et:
        conditions.append(AuditEvent.event_type.ilike(f"%{et}%"))
    if oc:
        conditions.append(AuditEvent.outcome.ilike(f"%{oc}%"))
    if conditions:
        statement = statement.where(*conditions)
        count_statement = count_statement.where(*conditions)
    total = int(db.scalar(count_statement) or 0)
    page_count = max(1, (total + AUDIT_PAGE_SIZE - 1) // AUDIT_PAGE_SIZE)
    page = min(page, page_count)
    events = list(
        db.scalars(
            statement.order_by(AuditEvent.occurred_at.desc())
            .offset((page - 1) * AUDIT_PAGE_SIZE)
            .limit(AUDIT_PAGE_SIZE)
        ).all()
    )
    return events, total, page, et, oc

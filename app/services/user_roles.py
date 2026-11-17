"""Transactional administrator promotion for existing accounts."""

from __future__ import annotations

import sqlite3
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, selectinload

from app.application.ports import RequestMetadata
from app.models import User
from app.services.audit import ADMIN_USER_ROLES_CHANGED, record_event
from app.services.auth_common import lock_administrator_role
from app.services.sessions import revoke_all_sessions


class PromotionResult(StrEnum):
    PROMOTED = "promoted"
    ALREADY_ADMINISTRATOR = "already_administrator"


class PromotionError(StrEnum):
    FORBIDDEN = "administrator_required"
    NOT_FOUND = "user_not_found"
    INELIGIBLE = "target_ineligible"
    ROLE_UNAVAILABLE = "administrator_role_unavailable"
    WRITE_CONFLICT = "write_conflict"


class PromotionRejected(Exception):
    def __init__(self, code: PromotionError) -> None:
        super().__init__(code.value)
        self.code = code


def is_promotion_eligible(user: User, authentication_mode: str) -> bool:
    """Check the account status, verification, and credential prerequisites for promotion."""
    return (
        user.is_active
        and user.email_verified_at is not None
        and (authentication_mode == "trusted_header" or bool(user.password_hash))
    )


def _is_write_conflict(exc: DBAPIError) -> bool:
    if exc.connection_invalidated:
        return False
    original = exc.orig
    if isinstance(original, sqlite3.Error):
        code = getattr(original, "sqlite_errorcode", 0)
        # Extended codes such as SQLITE_BUSY_SNAPSHOT retain the primary code in the low byte.
        return (code & 0xFF) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    return sqlstate in {"40001", "40P01", "55P03"}


def promote_user(
    db: Session,
    *,
    actor_id: str,
    target_id: str,
    authentication_mode: str,
    request: RequestMetadata | None = None,
) -> PromotionResult:
    """Add the role, invalidate the target's sessions, and audit in one transaction."""
    try:
        administrator = lock_administrator_role(db)
        if administrator is None:
            raise PromotionRejected(PromotionError.ROLE_UNAVAILABLE)

        # PostgreSQL refresh holds a session row while writing an audit event with user
        # foreign keys. NO KEY UPDATE lets those KEY SHARE checks finish during revocation.
        users = {
            user.id: user
            for user in db.scalars(
                select(User)
                .where(User.id.in_({actor_id, target_id}))
                .order_by(User.id)
                .with_for_update(key_share=True, of=User)
                .options(selectinload(User.roles))
                .execution_options(populate_existing=True)
            )
        }
        actor = users.get(actor_id)
        if actor is None or not actor.is_active or administrator not in actor.roles:
            raise PromotionRejected(PromotionError.FORBIDDEN)
        target = users.get(target_id)
        if target is None:
            raise PromotionRejected(PromotionError.NOT_FOUND)
        if not is_promotion_eligible(target, authentication_mode):
            raise PromotionRejected(PromotionError.INELIGIBLE)
        if administrator in target.roles:
            db.rollback()
            return PromotionResult.ALREADY_ADMINISTRATOR

        before_roles = target.role_names
        target.roles.append(administrator)
        target.security_version += 1
        revoke_all_sessions(db, target)
        record_event(
            db,
            ADMIN_USER_ROLES_CHANGED,
            request=request,
            actor=actor,
            target=target,
            detail={"before_roles": before_roles, "after_roles": target.role_names},
        )
        db.commit()
        return PromotionResult.PROMOTED
    except Exception as exc:
        db.rollback()
        if isinstance(exc, DBAPIError) and _is_write_conflict(exc):
            raise PromotionRejected(PromotionError.WRITE_CONFLICT) from exc
        raise

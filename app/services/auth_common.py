"""Shared auth exceptions, session token DTO, and role helpers."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db_compat import dialect_name, insert_for
from app.models import RefreshSession, Role, User

ADMINISTRATOR_ROLE = "administrator"


class AuthenticationError(ValueError):
    def __init__(self, message: str, *, reason: str = "invalid") -> None:
        super().__init__(message)
        self.reason = reason


class AccountLockedError(AuthenticationError):
    pass


class TokenFlowError(ValueError):
    pass


class RefreshOverlapRetry(TokenFlowError):
    """A stale refresh lacked the session proof required to recover its successor."""


class RegistrationPendingError(AuthenticationError):
    pass


@dataclass
class SessionTokens:
    access_token: str
    access_expires_in: int
    refresh_token: str
    session: RefreshSession
    security_version: int


def ensure_default_roles(db: Session) -> None:
    defaults = {
        "user": "Standard account holder",
        ADMINISTRATOR_ROLE: "Can manage users, invitations, roles, and audit records",
    }
    for name, description in defaults.items():
        values = {"name": name, "description": description}
        statement = (
            insert_for(db, Role).values(**values).on_conflict_do_nothing(index_elements=[Role.name])
        )
        db.execute(statement)
    db.commit()


def _lock_role(db: Session, name: str) -> Role | None:
    """Serialize enrollment writes against a single role row."""
    return db.scalar(select(Role).where(Role.name == name).with_for_update())


def lock_administrator_role(db: Session) -> Role | None:
    """Acquire the write lock shared by administrator account mutations."""
    if dialect_name(db) == "sqlite":
        # SQLite ignores FOR UPDATE. Take its write lock before reading account state.
        db.execute(
            update(Role)
            .where(Role.name == ADMINISTRATOR_ROLE)
            .values(name=Role.name)
            .execution_options(synchronize_session=False)
        )
    return _lock_role(db, ADMINISTRATOR_ROLE)


def lock_administrator_action(db: Session, actor: User) -> bool:
    """Serialize administrator-account mutations and revalidate the acting administrator."""
    administrator = lock_administrator_role(db)
    if administrator is None:
        return False
    current_actor = db.get(User, actor.id, populate_existing=True)
    if current_actor is None or not current_actor.is_active:
        return False
    db.expire(current_actor, ["roles"])
    return administrator in current_actor.roles

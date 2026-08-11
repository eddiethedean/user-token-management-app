"""Shared auth exceptions, session token DTO, and role helpers."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db_compat import insert_for
from app.models import RefreshSession, Role, User


class AuthenticationError(ValueError):
    pass


class AccountLockedError(AuthenticationError):
    pass


class TokenFlowError(ValueError):
    pass


class RegistrationPendingError(AuthenticationError):
    pass


@dataclass
class SessionTokens:
    access_token: str
    access_expires_in: int
    refresh_token: str
    session: RefreshSession


def ensure_default_roles(db: Session) -> None:
    defaults = {
        "user": "Standard account holder",
        "administrator": "Can manage users, invitations, roles, and audit records",
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


def lock_administrator_action(db: Session, actor: User) -> bool:
    """Serialize administrator-account mutations and revalidate the acting administrator."""
    administrator = db.scalar(select(Role).where(Role.name == "administrator").with_for_update())
    db.refresh(actor)
    db.expire(actor, ["roles"])
    return bool(administrator and actor.is_active and administrator in actor.roles)

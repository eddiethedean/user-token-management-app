"""Shared auth exceptions, session token DTO, and role helpers."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

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
    dialect = db.get_bind().dialect.name
    for name, description in defaults.items():
        values = {"name": name, "description": description}
        if dialect == "postgresql":
            statement = (
                postgresql_insert(Role)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[Role.name])
            )
        elif dialect == "sqlite":
            statement = (
                sqlite_insert(Role)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[Role.name])
            )
        else:
            if not db.scalar(select(Role.id).where(Role.name == name)):
                db.add(Role(**values))
            continue
        db.execute(statement)
    db.commit()


def _lock_role_catalog(db: Session) -> dict[str, Role]:
    """Serialize low-volume enrollment writes against stable role rows."""
    return {
        role.name: role
        for role in db.scalars(select(Role).order_by(Role.name).with_for_update()).all()
    }


def lock_administrator_action(db: Session, actor: User) -> bool:
    """Serialize administrator-account mutations and revalidate the acting administrator."""
    administrator = db.scalar(select(Role).where(Role.name == "administrator").with_for_update())
    db.refresh(actor)
    db.expire(actor, ["roles"])
    return bool(administrator and actor.is_active and administrator in actor.roles)

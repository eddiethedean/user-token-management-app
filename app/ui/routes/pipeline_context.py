"""Thread/session adapters shared by pipeline interaction routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeVar

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import Settings
from app.connectors.registry import catalog_browser_for
from app.database import SessionLocal
from app.models import User
from app.services.catalogs import UserCatalog

ResultT = TypeVar("ResultT")


class WithUserSession(Protocol):
    def __call__(
        self,
        settings: Settings,
        user_id: str,
        operation: Callable[[Session, User], ResultT],
    ) -> ResultT: ...


class WithUserCatalog(Protocol):
    def __call__(
        self,
        settings: Settings,
        user_id: str,
        request: Request,
        operation: Callable[[UserCatalog], ResultT],
    ) -> ResultT: ...


def with_user_session(
    settings: Settings, user_id: str, operation: Callable[[Session, User], ResultT]
) -> ResultT:
    """Run synchronous pipeline work with a session owned by the calling thread."""

    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
        return operation(db, user)


def with_user_catalog(
    settings: Settings,
    user_id: str,
    request: Request,
    operation: Callable[[UserCatalog], ResultT],
) -> ResultT:
    """Run a catalog operation with an owner-scoped session and resolver."""

    return with_user_session(
        settings,
        user_id,
        lambda db, user: operation(
            UserCatalog(
                db,
                settings,
                user,
                request=request,
                browser_resolver=catalog_browser_for,
            )
        ),
    )

"""Thread/session adapters shared by pipeline interaction routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeVar

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.application.catalogs import CatalogAccess, CatalogOperationRunner
from app.application.dto import ActorContext
from app.application.ports import RequestMetadata
from app.config import Settings
from app.database import current_session_factory
from app.infrastructure.runtime import ExecutionRuntime, RuntimeAdmissionError
from app.models import User
from app.security.client import client_ip

ResultT = TypeVar("ResultT")


async def run_owned_sync(
    request: Request, function: Callable[..., ResultT], *args: object
) -> ResultT:
    """Run request-owned synchronous work through the captured runtime."""

    runtime = getattr(request.state, "execution", None)
    if not isinstance(runtime, ExecutionRuntime):
        raise RuntimeAdmissionError("The request has no admitted execution runtime.")
    return await runtime.run_owned_sync(function, *args)  # type: ignore[return-value]


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
        operation: Callable[[CatalogAccess], ResultT],
    ) -> ResultT: ...


def request_metadata(settings: Settings, request: Request) -> RequestMetadata:
    """Resolve request facts once at the HTTP boundary."""

    return RequestMetadata(
        request_id=getattr(request.state, "request_id", ""),
        source_ip=client_ip(request, settings),
    )


def with_user_session(
    settings: Settings, user_id: str, operation: Callable[[Session, User], ResultT]
) -> ResultT:
    """Run synchronous pipeline work with a session owned by the calling thread."""

    with current_session_factory()() as db:
        user = db.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
        return operation(db, user)


def with_user_catalog(
    settings: Settings,
    user_id: str,
    request: Request,
    operation: Callable[[CatalogAccess], ResultT],
    catalog_runner: CatalogOperationRunner,
) -> ResultT:
    """Run a catalog operation through the infrastructure-owned runner."""

    metadata = request_metadata(settings, request)
    actor = ActorContext(
        user_id=user_id,
        request_id=metadata.request_id,
        source_ip=metadata.source_ip,
    )
    return catalog_runner(actor, operation)

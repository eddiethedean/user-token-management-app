"""Composition adapter for owner-scoped catalog operations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from app.application.catalogs import CatalogAccess
from app.application.dto import ActorContext
from app.application.ports import RequestMetadata
from app.connectors.registry import catalog_browser_for
from app.infrastructure.persistence.catalog_cache import SqlAlchemyCatalogCache
from app.infrastructure.runtime import ExecutionRuntime
from app.infrastructure.security.credentials import SqlAlchemyCredentialResolver
from app.security.client import client_ip
from app.services.catalogs import UserCatalog

ResultT = TypeVar("ResultT")


class SqlAlchemyCatalogOperationRunner:
    """Run one owner-scoped catalog operation inside an owned runtime."""

    def __init__(self, runtime: ExecutionRuntime) -> None:
        self._runtime = runtime

    def __call__(
        self, actor: ActorContext, operation: Callable[[CatalogAccess], ResultT]
    ) -> ResultT:
        with self._runtime.operation_scope():
            with self._runtime.context():
                with self._runtime.sessions() as db:
                    from app.models import User

                    user = db.get(User, actor.user_id)
                    if user is None:
                        from fastapi import HTTPException, status

                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
                        )
                    catalog = UserCatalog(
                        db,
                        self._runtime.execution_settings,
                        user,
                        request=None,
                        browser_resolver=catalog_browser_for,
                        cache=SqlAlchemyCatalogCache(db, self._runtime.execution_settings, user),
                        credential_resolver=SqlAlchemyCredentialResolver(
                            db,
                            self._runtime.execution_settings,
                            user,
                            RequestMetadata(
                                request_id=actor.request_id,
                                source_ip=actor.source_ip,
                            ),
                        ),
                    )
                    return operation(catalog)


def build_catalog_runner(runtime: ExecutionRuntime) -> SqlAlchemyCatalogOperationRunner:
    return SqlAlchemyCatalogOperationRunner(runtime)


def build_user_catalog(db: Any, settings: Any, user: Any, request: Any):
    metadata = (
        RequestMetadata(
            request_id=getattr(getattr(request, "state", None), "request_id", ""),
            source_ip=client_ip(request, settings),
        )
        if request is not None
        else None
    )
    return build_user_catalog_with_metadata(db, settings, user, metadata, request=request)


def build_user_catalog_with_metadata(
    db: Any,
    settings: Any,
    user: Any,
    metadata: RequestMetadata | None,
    *,
    request: Any = None,
):
    return UserCatalog(
        db,
        settings,
        user,
        request=request,
        browser_resolver=catalog_browser_for,
        cache=SqlAlchemyCatalogCache(db, settings, user),
        credential_resolver=SqlAlchemyCredentialResolver(db, settings, user, metadata),
    )

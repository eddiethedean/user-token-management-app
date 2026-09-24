"""SQLAlchemy adapter for the owner-scoped catalog cache port."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.ports import CatalogCache
from app.config import Settings
from app.db_compat import insert_for
from app.models import PipelineCatalogCache, User, new_id, utcnow


class SqlAlchemyCatalogCache(CatalogCache):
    def __init__(self, db: Session, settings: Settings, user: User) -> None:
        self.db = db
        self.settings = settings
        self.user = user

    def get(self, provider: str, namespace: str) -> dict[str, Any] | None:
        row = self.db.scalar(
            select(PipelineCatalogCache).where(
                PipelineCatalogCache.user_id == self.user.id,
                PipelineCatalogCache.provider == provider.casefold(),
                PipelineCatalogCache.namespace == namespace,
                PipelineCatalogCache.expires_at > utcnow(),
            )
        )
        if row is None:
            return None
        try:
            payload = json.loads(row.payload_json)
        except (TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def put(self, provider: str, namespace: str, payload: dict[str, Any]) -> None:
        provider_id = provider.casefold()
        now = utcnow()
        values = {
            "id": new_id(),
            "user_id": self.user.id,
            "provider": provider_id,
            "namespace": namespace,
            "payload_json": json.dumps(payload, separators=(",", ":")),
            "fetched_at": now,
            "expires_at": now + timedelta(seconds=self.settings.pipeline_catalog_ttl_seconds),
        }
        statement = insert_for(self.db, PipelineCatalogCache).values(**values)
        self.db.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    PipelineCatalogCache.user_id,
                    PipelineCatalogCache.provider,
                    PipelineCatalogCache.namespace,
                ],
                set_={
                    "payload_json": values["payload_json"],
                    "fetched_at": now,
                    "expires_at": values["expires_at"],
                },
            )
        )
        self.db.commit()

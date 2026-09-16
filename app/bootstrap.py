"""Explicit composition root for application-owned dependencies."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings, get_settings
from app.connectors.registry import ConnectorRegistry, load_builtin_connectors
from app.infrastructure.persistence.database import DatabaseRuntime, create_database
from app.infrastructure.runtime import ExecutionRuntime


@dataclass
class ApplicationComposition:
    """Resources owned by one app instance.

    Routes still use compatibility module adapters while they are migrated;
    new code should resolve providers and sessions from this composition.
    """

    settings: Settings
    database: DatabaseRuntime
    connectors: ConnectorRegistry
    execution: ExecutionRuntime

    @classmethod
    def create(cls, settings: Settings | None = None) -> ApplicationComposition:
        cfg = settings or get_settings()
        builder = load_builtin_connectors(
            demo=cfg.is_demo_mode, registry=ConnectorRegistry(settings=cfg), settings=cfg
        )
        database = create_database(cfg)
        return cls(
            settings=cfg,
            database=database,
            connectors=builder,
            execution=ExecutionRuntime(cfg, database.sessions, builder),
        )

    def close(self) -> None:
        self.database.dispose()


def compose_application(settings: Settings | None = None) -> ApplicationComposition:
    return ApplicationComposition.create(settings)

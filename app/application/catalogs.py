"""Framework-neutral catalog contracts used by application and UI code."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol, TypeVar

from app.application.dto import ActorContext
from app.domain.catalogs import CatalogPage, ObjectSchema, RemoteNamespace
from app.domain.locators import Locator, WritePolicy

ResultT = TypeVar("ResultT")


class CatalogAccess(Protocol):
    def list_namespaces(self, provider: str) -> Sequence[RemoteNamespace]: ...
    def list_objects(self, provider: str, namespace: str) -> CatalogPage: ...
    def inspect_object(self, provider: str, locator: Locator) -> ObjectSchema: ...
    def inspect_object_fresh(self, provider: str, locator: Locator) -> ObjectSchema: ...
    def preflight_source(self, provider: str, locator: Locator) -> ObjectSchema: ...
    def preflight_destination(
        self,
        provider: str,
        locator: Locator,
        source_schema: ObjectSchema,
        write_policy: WritePolicy,
    ) -> ObjectSchema | None: ...
    def count_rows(self, provider: str, locator: Locator) -> int | None: ...
    def default_branch(self, provider: str) -> str: ...
    def branch_for_namespace(self, provider: str, namespace: str) -> str: ...


class CatalogOperationRunner(Protocol):
    def __call__(
        self, actor: ActorContext, operation: Callable[[CatalogAccess], ResultT]
    ) -> ResultT: ...


CatalogOperation = Callable[[CatalogAccess], ResultT]

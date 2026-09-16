"""Framework-neutral catalog values shared by application and connectors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.domain.locators import Locator


@dataclass(frozen=True)
class RemoteNamespace:
    name: str
    display_name: str
    kind: str = "namespace"


@dataclass(frozen=True)
class ProvisionedDataset:
    dataset_rid: str
    name: str
    parent_folder_rid: str
    branch: str = "master"


@dataclass(frozen=True)
class RemoteObject:
    name: str
    display_name: str
    locator: Locator
    estimated_rows: int | None = None
    size_bytes: int | None = None
    updated_at: str = ""
    format: str = ""


@dataclass(frozen=True)
class CatalogPage:
    items: tuple[RemoteObject, ...]
    cursor: str | None = None


@dataclass(frozen=True)
class ColumnSchema:
    name: str
    data_type: str
    nullable: bool = True
    example: str = ""


@dataclass(frozen=True)
class ObjectSchema:
    locator: Locator
    columns: tuple[ColumnSchema, ...]
    primary_key: tuple[str, ...] = ()
    unique_constraints: tuple[tuple[str, ...], ...] = ()
    estimated_rows: int | None = None

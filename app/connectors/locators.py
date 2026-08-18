"""Versioned locator and write-policy contracts. Unknown kinds fail closed."""

from __future__ import annotations

import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

DATASET_RID_PATTERN = re.compile(
    r"^ri\.[A-Za-z0-9._-]+\.[A-Za-z0-9._-]+\.dataset\.[A-Za-z0-9._-]+$"
)
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
FOUNDATION_FILE_PATTERN = re.compile(r"^[A-Za-z0-9._/-]{1,240}$")
UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class PostgresTableLocator(BaseModel):
    kind: Literal["postgres_table"] = "postgres_table"
    schema_name: str = Field(alias="schema", min_length=1, max_length=63)
    table: str = Field(min_length=1, max_length=63)

    model_config = {"populate_by_name": True}

    @field_validator("schema_name", "table")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if not IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError(
                "PostgreSQL identifiers must be unquoted letters, numbers, or underscores."
            )
        return value


class FoundryDatasetFilesLocator(BaseModel):
    kind: Literal["foundry_dataset_files"] = "foundry_dataset_files"
    dataset_rid: str
    branch: str = Field(min_length=1, max_length=80)
    file_paths: list[str] | Literal["all_supported"] = "all_supported"

    @field_validator("dataset_rid")
    @classmethod
    def validate_rid(cls, value: str) -> str:
        if not DATASET_RID_PATTERN.fullmatch(value):
            raise ValueError("Enter a valid Foundry dataset RID.")
        return value

    @field_validator("file_paths")
    @classmethod
    def validate_paths(
        cls, value: list[str] | Literal["all_supported"]
    ) -> list[str] | Literal["all_supported"]:
        if value == "all_supported":
            return value
        if not value:
            raise ValueError("Select at least one dataset file.")
        for path in value:
            if (
                path.startswith("/")
                or ".." in path.split("/")
                or not FOUNDATION_FILE_PATTERN.fullmatch(path)
            ):
                raise ValueError("Dataset file paths must be relative and URL-safe.")
        return value


class FoundryUploadLocator(BaseModel):
    kind: Literal["foundry_upload"] = "foundry_upload"
    dataset_rid: str
    branch: str = Field(min_length=1, max_length=80)
    file_name: str
    publication: Literal["preview_upload"] = "preview_upload"

    @field_validator("dataset_rid")
    @classmethod
    def validate_rid(cls, value: str) -> str:
        if not DATASET_RID_PATTERN.fullmatch(value):
            raise ValueError("Enter a valid Foundry dataset RID.")
        return value

    @field_validator("file_name")
    @classmethod
    def validate_file_name(cls, value: str) -> str:
        if (
            "/" in value
            or "\\" in value
            or ".." in value
            or not FOUNDATION_FILE_PATTERN.fullmatch(value)
        ):
            raise ValueError("Destination filenames must be a single relative path segment.")
        if not value.endswith(".parquet"):
            raise ValueError("Foundry uploads must use a .parquet filename.")
        return value


class CsvUploadLocator(BaseModel):
    kind: Literal["csv_upload"] = "csv_upload"
    upload_id: str
    checksum_sha256: str = Field(min_length=64, max_length=64)

    @field_validator("upload_id")
    @classmethod
    def validate_upload_id(cls, value: str) -> str:
        if not UUID_PATTERN.fullmatch(value):
            raise ValueError("CSV upload id is invalid.")
        return value

    @field_validator("checksum_sha256")
    @classmethod
    def validate_checksum(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("CSV checksum is invalid.")
        return value


Locator = Annotated[
    PostgresTableLocator | FoundryDatasetFilesLocator | FoundryUploadLocator | CsvUploadLocator,
    Field(discriminator="kind"),
]


class PostgresAppendPolicy(BaseModel):
    kind: Literal["postgres_append"] = "postgres_append"


class PostgresUpsertPolicy(BaseModel):
    kind: Literal["postgres_upsert"] = "postgres_upsert"
    conflict_columns: list[str] = Field(min_length=1)
    action: Literal["update", "ignore"] = "ignore"

    @field_validator("conflict_columns")
    @classmethod
    def validate_conflict_columns(cls, value: list[str]) -> list[str]:
        for column in value:
            if not IDENTIFIER_PATTERN.fullmatch(column):
                raise ValueError("Conflict columns must be valid PostgreSQL identifiers.")
        return value


class PostgresReplacePolicy(BaseModel):
    kind: Literal["postgres_replace"] = "postgres_replace"
    schema_policy: Literal["require_compatible", "recreate"] = "require_compatible"


class FoundryReplaceFilePolicy(BaseModel):
    kind: Literal["foundry_replace_file"] = "foundry_replace_file"
    publication: Literal["preview_upload"] = "preview_upload"


WritePolicy = Annotated[
    PostgresAppendPolicy | PostgresUpsertPolicy | PostgresReplacePolicy | FoundryReplaceFilePolicy,
    Field(discriminator="kind"),
]


class DefinitionSnapshot(BaseModel):
    version: int = 2
    name: str
    source_provider: str
    destination_provider: str
    source: Locator
    destination: Locator
    write_policy: WritePolicy
    source_upload_id: str | None = None


def postgres_table(schema: str, table: str) -> PostgresTableLocator:
    return PostgresTableLocator.model_validate({"schema": schema, "table": table})


def parse_locator(payload: object) -> Locator:
    from pydantic import TypeAdapter

    return TypeAdapter(Locator).validate_python(payload)


def parse_write_policy(payload: object) -> WritePolicy:
    from pydantic import TypeAdapter

    return TypeAdapter(WritePolicy).validate_python(payload)


def parse_snapshot(payload: object) -> DefinitionSnapshot:
    if isinstance(payload, str):
        payload = json.loads(payload)
    return DefinitionSnapshot.model_validate(payload)

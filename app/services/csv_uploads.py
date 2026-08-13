"""Owner-scoped CSV uploads and conservative column profiling."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import PurePosixPath

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import PipelineUpload, User, new_id
from app.services.audit import record_event

MAX_CSV_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_CSV_COLUMNS = 200
MAX_CSV_CELL_CHARACTERS = 131_072
_INTEGER_PATTERN = re.compile(r"^[+-]?\d+$")
_DECIMAL_PATTERN = re.compile(r"^[+-]?(?:\d+\.\d*|\d*\.\d+)(?:[eE][+-]?\d+)?$")


@dataclass(frozen=True)
class CsvColumnProfile:
    name: str
    inferred_type: str
    populated: int
    nulls: int
    example: str


@dataclass(frozen=True)
class CsvInspection:
    filename: str
    size_bytes: int
    row_count: int
    columns: tuple[CsvColumnProfile, ...]


def inspect_csv(filename: str, content: bytes) -> CsvInspection:
    safe_filename = _safe_csv_filename(filename)
    if not content:
        raise ValueError("Choose a non-empty CSV file.")
    if len(content) > MAX_CSV_UPLOAD_BYTES:
        raise ValueError("CSV files must be 5 MB or smaller in this demo.")
    if b"\x00" in content:
        raise ValueError("The selected file is not a valid text CSV.")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV files must use UTF-8 encoding.") from exc

    sample = text[:16_384]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel

    try:
        rows = csv.reader(io.StringIO(text, newline=""), dialect=dialect)
        raw_headers = next(rows)
    except (StopIteration, csv.Error) as exc:
        raise ValueError("The CSV must contain a header row.") from exc

    headers = [header.strip() for header in raw_headers]
    if not headers or any(not header for header in headers):
        raise ValueError("Every CSV column must have a name.")
    if len(headers) > MAX_CSV_COLUMNS:
        raise ValueError(f"CSV files may contain at most {MAX_CSV_COLUMNS} columns.")
    if any(len(header) > 128 for header in headers):
        raise ValueError("CSV column names must be 128 characters or fewer.")
    folded_headers = [header.casefold() for header in headers]
    if len(set(folded_headers)) != len(folded_headers):
        raise ValueError("CSV column names must be unique.")

    type_sets: list[set[str]] = [set() for _ in headers]
    populated = [0 for _ in headers]
    nulls = [0 for _ in headers]
    examples = ["" for _ in headers]
    row_count = 0
    try:
        for row in rows:
            if not row or all(not value.strip() for value in row):
                continue
            if len(row) != len(headers):
                raise ValueError(
                    f"Row {row_count + 2} has {len(row)} values; expected {len(headers)}."
                )
            row_count += 1
            for index, raw_value in enumerate(row):
                value = raw_value.strip()
                if len(value) > MAX_CSV_CELL_CHARACTERS:
                    raise ValueError("A CSV cell exceeds the 128 KB demo limit.")
                if not value:
                    nulls[index] += 1
                    continue
                populated[index] += 1
                type_sets[index].add(_value_type(value))
                if not examples[index]:
                    examples[index] = value[:80]
    except csv.Error as exc:
        raise ValueError("The CSV could not be parsed consistently.") from exc

    columns = tuple(
        CsvColumnProfile(
            name=header,
            inferred_type=_merge_types(type_sets[index]),
            populated=populated[index],
            nulls=nulls[index],
            example=examples[index],
        )
        for index, header in enumerate(headers)
    )
    return CsvInspection(
        filename=safe_filename,
        size_bytes=len(content),
        row_count=row_count,
        columns=columns,
    )


def store_csv_upload(
    db: Session,
    *,
    user: User,
    filename: str,
    content_type: str,
    content: bytes,
    request: Request | None = None,
) -> tuple[PipelineUpload, CsvInspection]:
    inspection = inspect_csv(filename, content)
    upload = PipelineUpload(
        id=new_id(),
        user_id=user.id,
        filename=inspection.filename,
        content_type=(content_type or "text/csv")[:100],
        size_bytes=inspection.size_bytes,
        row_count=inspection.row_count,
        column_count=len(inspection.columns),
        columns_json=json.dumps(
            [asdict(column) for column in inspection.columns], separators=(",", ":")
        ),
        checksum_sha256=hashlib.sha256(content).hexdigest(),
        content=content,
    )
    db.add(upload)
    record_event(
        db,
        "pipeline.csv_uploaded",
        request=request,
        actor=user,
        target=user,
        detail={
            "upload_id": upload.id,
            "filename": inspection.filename,
            "rows": inspection.row_count,
            "columns": len(inspection.columns),
            "size_bytes": inspection.size_bytes,
        },
    )
    db.commit()
    db.refresh(upload)
    return upload, inspection


def inspection_from_upload(upload: PipelineUpload) -> CsvInspection:
    try:
        raw_columns = json.loads(upload.columns_json)
        columns = tuple(CsvColumnProfile(**column) for column in raw_columns)
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        raise ValueError("The stored CSV profile is invalid.") from exc
    return CsvInspection(
        filename=upload.filename,
        size_bytes=upload.size_bytes,
        row_count=upload.row_count,
        columns=columns,
    )


def _safe_csv_filename(filename: str) -> str:
    normalized = PurePosixPath((filename or "").replace("\\", "/")).name.strip()
    normalized = "".join(character for character in normalized if ord(character) >= 32)
    if not normalized or not normalized.casefold().endswith(".csv"):
        raise ValueError("Choose a file with a .csv extension.")
    if len(normalized) > 180:
        raise ValueError("CSV filenames must be 180 characters or fewer.")
    return normalized


def _value_type(value: str) -> str:
    lowered = value.casefold()
    if lowered in {"true", "false"}:
        return "boolean"
    if _INTEGER_PATTERN.fullmatch(value):
        return "integer"
    if _DECIMAL_PATTERN.fullmatch(value):
        return "decimal"
    try:
        if "t" in lowered or " " in value:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return "datetime"
        date.fromisoformat(value)
        return "date"
    except ValueError:
        return "text"


def _merge_types(types: set[str]) -> str:
    if not types:
        return "empty"
    if len(types) == 1:
        return next(iter(types))
    if types <= {"integer", "decimal"}:
        return "decimal"
    if types <= {"date", "datetime"}:
        return "datetime"
    return "text"

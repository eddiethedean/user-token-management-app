"""Owner-scoped CSV uploads and conservative column profiling."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath

from fastapi import Request
from sqlalchemy.orm import Session

from app.domain.csv_inference import (
    MAX_CSV_DECIMAL_PRECISION,
    CsvColumnProfile,
    csv_dialect,
    csv_headers,
    profile_csv_rows,
)
from app.models import PipelineUpload, User, new_id
from app.services.audit import record_event

MAX_CSV_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_CSV_COLUMNS = 200
MAX_CSV_CELL_CHARACTERS = 131_072


@dataclass(frozen=True)
class CsvInspection:
    filename: str
    size_bytes: int
    row_count: int
    columns: tuple[CsvColumnProfile, ...]
    delimiter: str = ","
    quote_char: str = '"'


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
    dialect = csv_dialect(sample)

    try:
        rows = csv.reader(io.StringIO(text, newline=""), dialect=dialect)
        raw_headers = next(rows)
    except (StopIteration, csv.Error) as exc:
        raise ValueError("The CSV must contain a header row.") from exc

    headers = csv_headers(raw_headers, max_columns=MAX_CSV_COLUMNS)
    row_count, columns = profile_csv_rows(
        headers, rows, max_cell_characters=MAX_CSV_CELL_CHARACTERS
    )
    return CsvInspection(
        filename=safe_filename,
        size_bytes=len(content),
        row_count=row_count,
        columns=columns,
        delimiter=dialect.delimiter,
        quote_char=dialect.quotechar or '"',
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
            {
                "delimiter": inspection.delimiter,
                "quote_char": inspection.quote_char,
                "columns": [asdict(column) for column in inspection.columns],
            },
            separators=(",", ":"),
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
        if isinstance(raw_columns, dict):
            legacy_quote_char = "quote_char" not in raw_columns
            delimiter = str(raw_columns.get("delimiter") or ",")
            quote_char = str(raw_columns.get("quote_char") or '"')
            raw_columns = raw_columns.get("columns", [])
        else:
            legacy_quote_char = True
            delimiter = ","
            quote_char = '"'
        columns = tuple(CsvColumnProfile(**column) for column in raw_columns)
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        raise ValueError("The stored CSV profile is invalid.") from exc
    if legacy_quote_char:
        content = upload.content
        if isinstance(content, str):
            content = content.encode("utf-8")
        if not isinstance(content, bytes):
            raise ValueError("The stored CSV dialect profile is incomplete.")
        return inspect_csv(upload.filename, content)
    if any(
        isinstance(column, dict)
        and column.get("inferred_type") == "datetime"
        and "timezone_aware" not in column
        for column in raw_columns
    ):
        content = upload.content
        if isinstance(content, str):
            content = content.encode("utf-8")
        if not isinstance(content, bytes):
            raise ValueError("The stored CSV timestamp profile is incomplete.")
        return inspect_csv(upload.filename, content)
    if any(
        column.inferred_type == "decimal" and column.decimal_precision == 0 for column in columns
    ):
        content = upload.content
        if isinstance(content, str):
            content = content.encode("utf-8")
        if not isinstance(content, bytes):
            raise ValueError("The stored CSV decimal profile is incomplete.")
        return inspect_csv(upload.filename, content)
    if any(
        column.inferred_type == "decimal" and column.decimal_precision > MAX_CSV_DECIMAL_PRECISION
        for column in columns
    ):
        raise ValueError(f"CSV decimal precision cannot exceed {MAX_CSV_DECIMAL_PRECISION} digits.")
    return CsvInspection(
        filename=upload.filename,
        size_bytes=upload.size_bytes,
        row_count=upload.row_count,
        columns=columns,
        delimiter=delimiter,
        quote_char=quote_char,
    )


def _safe_csv_filename(filename: str) -> str:
    normalized = PurePosixPath((filename or "").replace("\\", "/")).name.strip()
    normalized = "".join(character for character in normalized if ord(character) >= 32)
    if not normalized or not normalized.casefold().endswith(".csv"):
        raise ValueError("Choose a file with a .csv extension.")
    if len(normalized) > 180:
        raise ValueError("CSV filenames must be 180 characters or fewer.")
    return normalized

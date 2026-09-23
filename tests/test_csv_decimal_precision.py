"""Exact decimal profiling and parsing for uploaded CSV sources."""

from __future__ import annotations

import json
from decimal import Decimal

import polars as pl
import pytest

from app.connectors.csv_source import CsvSourceConnector
from app.connectors.locators import CsvUploadLocator
from app.models import PipelineUpload
from app.services.csv_uploads import inspect_csv, inspection_from_upload


def _csv_credentials(content: bytes, inspection) -> dict[str, str | bytes]:
    return {
        "content": content,
        "delimiter": inspection.delimiter,
        "columns": json.dumps([column.name for column in inspection.columns]),
        "column_types": json.dumps([column.inferred_type for column in inspection.columns]),
        "column_decimal_specs": json.dumps(
            [
                {"precision": column.decimal_precision, "scale": column.decimal_scale}
                for column in inspection.columns
            ]
        ),
    }


def _locator() -> CsvUploadLocator:
    return CsvUploadLocator(
        upload_id="00000000-0000-0000-0000-000000000001",
        checksum_sha256="0" * 64,
    )


def test_csv_decimal_profile_and_extraction_preserve_exact_values() -> None:
    content = b"amount,rate\n9007199254740993.01,1\n1,2.5\n-2.50,3.25\n"
    inspection = inspect_csv("exact.csv", content)
    assert [
        (column.inferred_type, column.decimal_precision, column.decimal_scale)
        for column in inspection.columns
    ] == [
        ("decimal", 18, 2),
        ("decimal", 3, 2),
    ]

    credentials = _csv_credentials(content, inspection)
    batches = list(
        CsvSourceConnector().extract(
            credentials,
            _locator(),
            batch_rows=1_000,
            batch_bytes=1_024,
        )
    )
    frame = pl.concat([batch.frame for batch in batches])

    assert frame.schema["amount"] == pl.Decimal(precision=18, scale=2)
    assert frame["amount"].to_list() == [
        Decimal("9007199254740993.01"),
        Decimal("1.00"),
        Decimal("-2.50"),
    ]
    assert frame["rate"].to_list() == [
        Decimal("1.00"),
        Decimal("2.50"),
        Decimal("3.25"),
    ]


def test_whitespace_padded_decimal_cells_are_normalized_without_losing_precision() -> None:
    content = b'amount,label\n"1.20 "," keep "\n'
    inspection = inspect_csv("padded.csv", content)

    assert inspection.columns[0].inferred_type == "decimal"
    assert (inspection.columns[0].decimal_precision, inspection.columns[0].decimal_scale) == (3, 2)

    batches = list(
        CsvSourceConnector().extract(
            _csv_credentials(content, inspection),
            _locator(),
            batch_rows=1_000,
            batch_bytes=1_024,
        )
    )

    assert batches[0].frame.schema["amount"] == pl.Decimal(precision=3, scale=2)
    assert batches[0].frame["amount"].to_list() == [Decimal("1.20")]
    assert batches[0].frame["label"].to_list() == [" keep "]


def test_legacy_csv_profile_recomputes_missing_decimal_shape() -> None:
    content = b"amount\n9007199254740993.01\n"
    upload = PipelineUpload(
        id="00000000-0000-0000-0000-000000000001",
        user_id="00000000-0000-0000-0000-000000000002",
        filename="legacy.csv",
        content_type="text/csv",
        content=content,
        size_bytes=len(content),
        row_count=1,
        column_count=1,
        checksum_sha256="0" * 64,
        columns_json=json.dumps(
            {
                "delimiter": ",",
                "columns": [
                    {
                        "name": "amount",
                        "inferred_type": "decimal",
                        "populated": 1,
                        "nulls": 0,
                        "example": "9007199254740993.01",
                    }
                ],
            }
        ),
    )

    inspection = inspection_from_upload(upload)

    assert inspection.columns[0].decimal_precision == 18
    assert inspection.columns[0].decimal_scale == 2


def test_csv_decimal_over_precision_limit_is_rejected_during_scan() -> None:
    content = b"amount\n123456789012345678901234567890123456789.01\n"

    with pytest.raises(ValueError, match="precision cannot exceed 38 digits"):
        inspect_csv("wide.csv", content)

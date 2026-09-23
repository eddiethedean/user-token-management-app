"""Exact decimal profiling and parsing for uploaded CSV sources."""

from __future__ import annotations

import json
from decimal import Decimal

import polars as pl
import pytest

from app.connectors.csv_source import CsvSourceConnector
from app.connectors.errors import ConnectorError, TransferErrorCode
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


def test_csv_decimal_over_precision_limit_fails_before_destination_preparation() -> None:
    content = b"amount\n123456789012345678901234567890123456789.01\n"
    inspection = inspect_csv("wide.csv", content)
    credentials = _csv_credentials(content, inspection)

    with pytest.raises(ConnectorError) as excinfo:
        CsvSourceConnector().inspect_object(credentials, _locator())

    assert excinfo.value.code == TransferErrorCode.UNSUPPORTED_TYPE
    assert "precision of 38 digits" in str(excinfo.value)

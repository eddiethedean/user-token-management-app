"""CSV source connector and remaining connector-port coverage."""

from __future__ import annotations

import json

import polars as pl
import pytest

from app.connectors.base import bounded_frame_batches, map_http_status
from app.connectors.csv_source import CsvSourceConnector
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.fake import FakeCsvConnector
from app.connectors.locators import CsvUploadLocator
from app.connectors.registry import route_allowed
from app.connectors.tls import verify_hostname_policy
from app.services.csv_uploads import inspect_csv


def test_csv_source_inspects_and_extracts_batches() -> None:
    connector = CsvSourceConnector()
    locator = CsvUploadLocator(
        upload_id="11111111-1111-1111-1111-111111111111", checksum_sha256="a" * 64
    )
    credentials = {"content": "event_id,unit_name\n1,Alpha\n2,Bravo\n3,Charlie\n"}
    assert connector.test_connection(credentials).status == "connected"
    assert connector.list_namespaces(credentials)[0].name == "uploaded"
    assert connector.list_objects(credentials, "uploaded").items == ()
    inspected = connector.inspect_object(credentials, locator)
    assert inspected.estimated_rows == 3
    batches = list(connector.extract(credentials, locator, batch_rows=2, batch_bytes=1024))
    assert len(batches) == 2
    assert batches[0].row_count == 2
    with pytest.raises(ConnectorError):
        connector.prepare_destination(credentials, locator, inspected, None, run_id="x")  # type: ignore[arg-type]
    with pytest.raises(ConnectorError):
        connector.write_batch(None, batches[0])  # type: ignore[arg-type]
    with pytest.raises(ConnectorError):
        connector.finalize(None)  # type: ignore[arg-type]
    connector.abort(None)  # type: ignore[arg-type]


def test_csv_source_rejects_missing_content() -> None:
    connector = CsvSourceConnector()
    locator = CsvUploadLocator(
        upload_id="11111111-1111-1111-1111-111111111111", checksum_sha256="b" * 64
    )
    with pytest.raises(ConnectorError) as excinfo:
        connector.inspect_object({}, locator)
    assert excinfo.value.code == TransferErrorCode.SOURCE_NOT_FOUND


def test_csv_source_reuses_inspection_delimiter_and_normalized_headers() -> None:
    connector = CsvSourceConnector()
    locator = CsvUploadLocator(
        upload_id="11111111-1111-1111-1111-111111111111", checksum_sha256="c" * 64
    )
    credentials = {
        "content": " id ; value\n001;ok\n002;still text\n",
        "delimiter": ";",
        "columns": json.dumps(["id", "value"]),
        "column_types": json.dumps(["text", "text"]),
    }
    frame = connector.inspect_object(credentials, locator)
    assert [column.name for column in frame.columns] == ["id", "value"]
    assert frame.columns[0].data_type == "String"
    batches = list(connector.extract(credentials, locator, batch_rows=100, batch_bytes=10_000))
    assert batches[0].frame["id"].to_list() == ["001", "002"]


def test_csv_source_uses_profiled_types_instead_of_reinferring_them() -> None:
    content = (
        b"ready,service_date,observed_at,shift_time,amount,code\n"
        b"true,2026-09-23,2026-09-23T10:05:30Z,08:30:15,1e3,0012\n"
        b"false,2026-09-24,2026-09-24T11:05:30+02:00,10:45:00,2.5e2,0013\n"
    )
    inspection = inspect_csv("source.csv", content)
    assert [column.inferred_type for column in inspection.columns] == [
        "boolean",
        "date",
        "datetime",
        "time",
        "decimal",
        "text",
    ]
    assert inspection.columns[2].timezone_aware is True
    locator = CsvUploadLocator(
        upload_id="11111111-1111-1111-1111-111111111111", checksum_sha256="c" * 64
    )
    credentials = {
        "content": content,
        "columns": json.dumps([column.name for column in inspection.columns]),
        "column_types": json.dumps([column.inferred_type for column in inspection.columns]),
        "column_timezones": json.dumps([column.timezone_aware for column in inspection.columns]),
        "column_decimal_specs": json.dumps(
            [
                {"precision": column.decimal_precision, "scale": column.decimal_scale}
                for column in inspection.columns
            ]
        ),
    }
    frame = CsvSourceConnector()._frame(locator, credentials)
    assert frame.schema == {
        "ready": pl.Boolean,
        "service_date": pl.Date,
        "observed_at": pl.Datetime("us", time_zone="UTC"),
        "shift_time": pl.Time,
        "amount": pl.Decimal(precision=4, scale=0),
        "code": pl.String,
    }
    assert frame["code"].to_list() == ["0012", "0013"]


def test_csv_inference_keeps_mixed_naive_and_zoned_timestamps_as_text() -> None:
    inspection = inspect_csv(
        "mixed.csv",
        b"observed_at\n2026-09-23T10:05:30\n2026-09-24T11:05:30Z\n",
    )
    assert inspection.columns[0].inferred_type == "text"


def test_csv_inference_uses_exact_decimal_for_integers_beyond_bigint() -> None:
    inspection = inspect_csv("wide.csv", b"amount,identifier\n9223372036854775808,0012\n")
    assert inspection.columns[0].inferred_type == "decimal"
    assert inspection.columns[0].decimal_precision == 19
    assert inspection.columns[1].inferred_type == "text"


def test_shared_batch_boundary_enforces_rows_and_bytes() -> None:
    frame = pl.DataFrame(
        {
            "id": list(range(10)),
            "value": [f"payload-{index}" for index in range(10)],
        }
    )
    row_batches = list(bounded_frame_batches(frame, batch_rows=3, batch_bytes=10_000))
    byte_limit = int(frame.slice(0, 1).estimated_size()) * 3
    byte_batches = list(bounded_frame_batches(frame, batch_rows=100, batch_bytes=byte_limit))

    for batches in (row_batches, byte_batches):
        recombined = pl.concat([batch.frame for batch in batches])
        assert recombined.to_dicts() == frame.to_dicts()
        assert sum(batch.row_count for batch in batches) == frame.height
        assert all(batch.row_count == batch.frame.height for batch in batches)
        assert all(batch.byte_count == batch.frame.estimated_size() for batch in batches)
        assert all(
            batch.byte_count <= (10_000 if batches is row_batches else byte_limit)
            for batch in batches
        )
    assert all(batch.row_count <= 3 for batch in row_batches)

    empty = pl.DataFrame(schema={"id": pl.Int64, "value": pl.String})
    assert list(bounded_frame_batches(empty, batch_rows=3, batch_bytes=100)) == []

    oversized = pl.DataFrame({"value": ["x" * 500]})
    with pytest.raises(ConnectorError) as excinfo:
        list(bounded_frame_batches(oversized, batch_rows=10, batch_bytes=100))
    assert excinfo.value.code == TransferErrorCode.SOURCE_LIMIT_EXCEEDED


def test_fake_csv_cannot_be_a_destination() -> None:
    connector = FakeCsvConnector()
    with pytest.raises(ConnectorError):
        connector.prepare_destination({}, None, None, None, run_id="x")  # type: ignore[arg-type]


def test_route_allowed_and_http_status_mapping() -> None:
    from app.connectors.registry import load_builtin_connectors

    load_builtin_connectors(demo=True)
    assert route_allowed("csv", "postgres") is True
    assert route_allowed("mss", "mss") is True
    assert route_allowed("mss", "mcscop") is True
    assert route_allowed("mcscop", "postgres") is True
    assert route_allowed("postgres", "csv") is False
    assert route_allowed("csv", "csv") is False
    assert map_http_status(401) == TransferErrorCode.AUTHENTICATION_FAILED
    assert map_http_status(403) == TransferErrorCode.PERMISSION_DENIED
    assert map_http_status(404) == TransferErrorCode.SOURCE_NOT_FOUND
    assert map_http_status(404, for_destination=True) == TransferErrorCode.DESTINATION_NOT_FOUND
    assert map_http_status(409) == TransferErrorCode.DESTINATION_CONFLICT
    assert map_http_status(429) == TransferErrorCode.RATE_LIMITED
    assert map_http_status(503) == TransferErrorCode.PROVIDER_UNAVAILABLE
    verify_hostname_policy(https_only=True, hostname="foundry.example")


def test_verify_hostname_rejects_empty_host() -> None:
    from app.connectors.tls import TlsBootstrapError

    with pytest.raises(TlsBootstrapError):
        verify_hostname_policy(https_only=True, hostname="")

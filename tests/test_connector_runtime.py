"""CSV source connector and remaining connector-port coverage."""

from __future__ import annotations

import pytest

from app.connectors.base import map_http_status
from app.connectors.csv_source import CsvSourceConnector
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.fake import FakeCsvConnector
from app.connectors.locators import CsvUploadLocator
from app.connectors.registry import route_allowed
from app.connectors.tls import verify_hostname_policy


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


def test_fake_csv_cannot_be_a_destination() -> None:
    connector = FakeCsvConnector()
    with pytest.raises(ConnectorError):
        connector.prepare_destination({}, None, None, None, run_id="x")  # type: ignore[arg-type]


def test_route_allowed_and_http_status_mapping() -> None:
    from app.connectors.registry import load_builtin_connectors

    load_builtin_connectors(demo=True)
    assert route_allowed("csv", "postgres") is True
    assert route_allowed("mss", "mss") is False
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

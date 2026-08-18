"""PostgreSQL identifier and SQL composition guards."""

from __future__ import annotations

import pytest

from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.postgres import _ident, _pg_type


def test_postgres_identifiers_reject_injection() -> None:
    assert _ident("mission_orders") == "mission_orders"
    with pytest.raises(ConnectorError) as excinfo:
        _ident("public;drop")
    assert excinfo.value.code == TransferErrorCode.UNSUPPORTED_TYPE
    with pytest.raises(ConnectorError) as invalid:
        _ident("1invalid")
    assert invalid.value.code == TransferErrorCode.UNSUPPORTED_TYPE


def test_postgres_type_mapping_covers_common_polars_names() -> None:
    assert _pg_type("Int64") == "BIGINT"
    assert _pg_type("Utf8") == "TEXT"
    assert _pg_type("Boolean") == "BOOLEAN"
    assert _pg_type("mystery") == "TEXT"

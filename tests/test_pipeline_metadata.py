"""Pure metadata provenance and schema comparison coverage."""

from app.services.pipeline_metadata import manifest_metadata, provenance_label, schema_diff


def test_provenance_labels_are_user_readable() -> None:
    assert provenance_label("exact") == "Exact"
    assert provenance_label("provider_unavailable") == "Provider does not expose this fact"
    assert provenance_label("unknown") == "Unavailable"


def test_schema_diff_classifies_missing_extra_and_changed_columns() -> None:
    source = {
        "schema": {
            "columns": [
                {"name": "event_id", "data_type": "Int64", "nullable": False},
                {"name": "score", "data_type": "Float64", "nullable": True},
            ]
        }
    }
    destination = {
        "schema": {
            "columns": [
                {"name": "event_id", "data_type": "Int64", "nullable": False},
                {"name": "score", "data_type": "Decimal", "nullable": True},
                {"name": "loaded_at", "data_type": "Timestamp", "nullable": False},
            ]
        }
    }

    rows = schema_diff(source, destination)
    assert [(row["name"], row["status"]) for row in rows] == [
        ("event_id", "match"),
        ("loaded_at", "extra_destination"),
        ("score", "changed"),
    ]


def test_manifest_metadata_is_safe_and_explicit() -> None:
    assert manifest_metadata(
        rows=3,
        schema_available=True,
        row_provenance="exact",
        schema_provenance="captured",
    ) == {
        "rows": {"value": 3, "provenance": "exact"},
        "schema": {"available": True, "provenance": "captured"},
    }

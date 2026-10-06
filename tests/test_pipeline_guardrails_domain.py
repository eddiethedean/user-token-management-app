"""Value-free guardrail parsing and detection helpers."""

from __future__ import annotations

import json

import polars as pl
import pytest

from app.domain.pipelines.guardrails import (
    GuardrailAction,
    action_lookup,
    configured_marker_values,
    contains_supported_ssn,
    guardrail_scan_matches_source,
    guardrail_source_key,
    matching_metadata_markers,
    normalize_marker,
    parse_guardrail_action_values,
    parse_guardrail_scan_result,
    scan_ssn_frame,
)


def test_guardrail_actions_preserve_identifiers_and_reject_blank_or_control_names() -> None:
    action = GuardrailAction(detector="ssn", column=" ssn ", action="hash")
    assert action.column == " ssn "
    for invalid_name in (" ", "\n\t", "ssn\nname", "ssn\x7f"):
        with pytest.raises(ValueError):
            GuardrailAction(detector="ssn", column=invalid_name, action="hash")


def test_review_parsing_keeps_distinct_column_identifiers() -> None:
    values = [json.dumps(["ssn", "ssn", "hash"]), json.dumps(["ssn", " ssn ", "remove"])]
    parsed = parse_guardrail_action_values(values)
    assert action_lookup(parsed) == {("ssn", "ssn"): "hash", ("ssn", " ssn "): "remove"}
    findings = parse_guardrail_scan_result(
        json.dumps(
            [
                {"detector": "ssn", "column": "ssn", "count": 1},
                {"detector": "ssn", "column": " ssn ", "count": 2},
            ]
        )
    )
    assert [finding["column"] for finding in findings] == ["ssn", " ssn "]


def test_source_keys_and_action_values_are_bound_to_current_source() -> None:
    first = guardrail_source_key("MSS", "dataset", "file.parquet")
    second = guardrail_source_key("mss", "dataset", "file.parquet")
    csv_key = guardrail_source_key("csv", upload_id="upload-1")
    assert first == second
    assert csv_key != guardrail_source_key("csv", upload_id="upload-2")

    encoded = json.dumps(["ssn", "ssn", "hash", first])
    assert parse_guardrail_action_values([encoded], expected_source_key=first) == [
        GuardrailAction(detector="ssn", column="ssn", action="hash")
    ]
    assert parse_guardrail_action_values([encoded], expected_source_key="another-source") == []
    with pytest.raises(ValueError):
        parse_guardrail_action_values(["not-json"])
    with pytest.raises(ValueError):
        parse_guardrail_action_values([json.dumps(["unknown", "ssn", "hash"])])
    with pytest.raises(ValueError):
        parse_guardrail_action_values(
            [json.dumps(["ssn", "ssn", "hash"]), json.dumps(["ssn", "ssn", "remove"])]
        )


def test_source_bound_scan_results_validate_and_deduplicate_findings() -> None:
    key = "source-key"
    payload = json.dumps(
        {
            "source_key": key,
            "findings": [
                {"detector": "ssn", "column": "ssn", "count": 2},
                {"detector": "ssn", "column": "ssn", "count": 9},
            ],
        }
    )
    assert guardrail_scan_matches_source(payload, key)
    parsed = parse_guardrail_scan_result(payload, expected_source_key=key)
    assert parsed == [{"detector": "ssn", "source": "Content scan", "column": "ssn", "count": 2}]
    assert parse_guardrail_scan_result(payload, expected_source_key="other") == []
    assert not guardrail_scan_matches_source("invalid", key)
    with pytest.raises(ValueError):
        parse_guardrail_scan_result("invalid")
    with pytest.raises(ValueError):
        parse_guardrail_scan_result(json.dumps([{"detector": "pii", "column": "ssn", "count": 1}]))
    with pytest.raises(ValueError):
        parse_guardrail_scan_result(json.dumps([{"detector": "ssn", "column": "ssn", "count": 0}]))


def test_marker_matching_and_ssn_detection_handle_supported_forms_only() -> None:
    configured = configured_marker_values(" PII, sensitive;PHI\nCUI ")
    assert configured == frozenset({"pii", "sensitive", "phi", "cui"})
    assert normalize_marker("P.H.I.") == "phi"
    assert matching_metadata_markers(
        {"customMetadata": [{"key": "PII", "value": "yes"}, {"sensitive": True}]},
        configured,
    ) == ("pii", "sensitive")
    assert matching_metadata_markers({"pii": False}, configured) == ()

    assert contains_supported_ssn("123-45-6789")
    assert contains_supported_ssn("123 45 6789")
    assert contains_supported_ssn("123456789")
    assert not contains_supported_ssn("000-12-3456")
    assert not contains_supported_ssn("666-12-3456")
    assert not contains_supported_ssn("123-00-4567")
    assert not contains_supported_ssn("123-45-0000")
    assert not contains_supported_ssn(True)
    assert not contains_supported_ssn(None)


def test_scan_frame_counts_matches_without_returning_ignored_columns() -> None:
    frame = pl.DataFrame(
        {
            "ssn": ["123-45-6789", None, "not an ssn"],
            "tagged": ["123456789", "987654321", None],
        }
    )
    assert scan_ssn_frame(frame, ignored_columns=("tagged",)) == {"ssn": 1}
    assert scan_ssn_frame(frame) == {"ssn": 1, "tagged": 1}


def test_action_lookup_keys_detector_and_column() -> None:
    action = GuardrailAction(detector="foundry_metadata", column="email", action="remove")
    assert action_lookup([action]) == {("foundry_metadata", "email"): "remove"}

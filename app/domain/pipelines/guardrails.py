"""Sensitive-data guardrail decisions and value-free findings."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, field_validator

GuardrailDetector = Literal["foundry_metadata", "ssn"]
GuardrailActionName = Literal["hash", "remove"]


class GuardrailAction(BaseModel):
    """A saved decision for one detector and source column."""

    detector: GuardrailDetector
    column: str = Field(min_length=1, max_length=256)
    action: GuardrailActionName

    @field_validator("column")
    @classmethod
    def validate_column(cls, value: str) -> str:
        normalized = "".join(character for character in value.strip() if ord(character) >= 32)
        if not normalized or len(normalized) > 256:
            raise ValueError("Guardrail column names must be between 1 and 256 characters.")
        return normalized


@dataclass(frozen=True)
class GuardrailFinding:
    """A value-free summary of a column-level sensitivity finding."""

    detector: GuardrailDetector
    source: str
    column: str
    count: int | None = None
    action: GuardrailActionName | None = None
    outcome: str = "review_required"

    def as_dict(self) -> dict[str, object]:
        return {
            "detector": self.detector,
            "source": self.source,
            "column": self.column,
            "count": self.count,
            "action": self.action,
            "outcome": self.outcome,
        }


_SSN_PATTERN = re.compile(
    r"(?<!\d)(?!000|666|9\d\d)\d{3}[- ]?(?!00)\d{2}[- ]?(?!0000)\d{4}(?!\d)"
)
_SSN_NUMBER_PATTERN = re.compile(r"^\d{1,9}$")


def guardrail_source_key(
    provider: str,
    namespace: str = "",
    object_name: str = "",
    upload_id: str = "",
) -> str:
    """Bind review controls to the source selection that produced them."""

    provider_id = provider.casefold().strip()
    identity = (
        [provider_id, upload_id.strip()]
        if provider_id == "csv"
        else [provider_id, namespace.strip(), object_name.strip()]
    )
    canonical = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_guardrail_action_values(
    values: Sequence[str], *, expected_source_key: str | None = None
) -> list[GuardrailAction]:
    """Decode repeated form values and discard choices from another source."""

    actions: list[GuardrailAction] = []
    seen: set[tuple[str, str]] = set()
    for raw_value in values:
        try:
            payload = json.loads(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("A sensitive-data action selection is invalid.") from exc
        if (
            not isinstance(payload, list)
            or len(payload) not in {3, 4}
            or not all(isinstance(item, str) for item in payload)
        ):
            raise ValueError("A sensitive-data action selection is invalid.")
        detector, column, action = payload[:3]
        if not action:
            continue
        submitted_source_key = payload[3] if len(payload) == 4 else ""
        if expected_source_key is not None and submitted_source_key != expected_source_key:
            continue
        try:
            parsed = GuardrailAction(detector=detector, column=column, action=action)
        except ValueError as exc:
            raise ValueError("Choose Hash or Remove for each sensitive-data finding.") from exc
        key = (parsed.detector, parsed.column)
        if key in seen:
            raise ValueError("Each detector and column can have only one action.")
        seen.add(key)
        actions.append(parsed)
    return actions


def guardrail_scan_matches_source(value: str, expected_source_key: str) -> bool:
    """Check that preview findings belong to the currently selected source."""

    if not value:
        return False
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return False
    return (
        isinstance(payload, Mapping)
        and payload.get("source_key") == expected_source_key
        and isinstance(payload.get("findings"), list)
    )


def parse_guardrail_scan_result(
    value: str, *, expected_source_key: str | None = None
) -> list[dict[str, object]]:
    """Validate value-free findings and reject results from another source."""

    if not value:
        return []
    try:
        payload = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("The sensitive-data scan summary is invalid.") from exc
    if isinstance(payload, Mapping):
        if expected_source_key is not None and payload.get("source_key") != expected_source_key:
            return []
        payload = payload.get("findings")
    elif expected_source_key is not None:
        return []
    if not isinstance(payload, list) or len(payload) > 1_000:
        raise ValueError("The sensitive-data scan summary is invalid.")
    output: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for item in payload:
        if not isinstance(item, Mapping):
            raise ValueError("The sensitive-data scan summary is invalid.")
        try:
            detector = str(item.get("detector") or "")
            column = GuardrailAction(
                detector=detector,
                column=str(item.get("column") or ""),
                action="hash",
            ).column
            count = int(item.get("count") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("The sensitive-data scan summary is invalid.") from exc
        if detector != "ssn" or count < 1:
            raise ValueError("The sensitive-data scan summary is invalid.")
        key = (detector, column)
        if key in seen:
            continue
        seen.add(key)
        output.append(
            {
                "detector": "ssn",
                "source": "Content scan",
                "column": column,
                "count": count,
            }
        )
    return output


def configured_marker_values(value: str | Sequence[str] | None) -> frozenset[str]:
    """Normalize a comma-separated list of configured sensitivity markers."""

    if isinstance(value, str):
        candidates = re.split(r"[,;\n]", value)
    else:
        candidates = value or ()
    return frozenset(
        normalized
        for item in candidates
        if (normalized := normalize_marker(str(item)))
    )


def normalize_marker(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def matching_metadata_markers(metadata: object, configured: frozenset[str]) -> tuple[str, ...]:
    """Return only configured marker names found in a metadata tree.

    The result never contains arbitrary metadata text. Configured names are
    the only values that may cross the connector boundary.
    """

    if not configured:
        return ()
    hits: set[str] = set()

    def walk(value: object, *, key: str = "") -> None:
        if isinstance(value, Mapping):
            for child_key, child_value in value.items():
                walk(child_value, key=str(child_key))
            return
        if isinstance(value, (list, tuple, set, frozenset)):
            for child in value:
                walk(child, key=key)
            return
        if isinstance(value, bool):
            if value and normalize_marker(key) in configured:
                hits.add(normalize_marker(key))
            return
        if not isinstance(value, (str, int, float)):
            return
        text = str(value)
        tokens = {normalize_marker(token) for token in re.findall(r"[\w.-]+", text)}
        direct = normalize_marker(text)
        hits.update((tokens | {direct}) & configured)
        if normalize_marker(key) in configured and text.strip().casefold() in {
            "true",
            "yes",
            "1",
            "sensitive",
        }:
            hits.add(normalize_marker(key))

    walk(metadata)
    return tuple(sorted(hits))


def contains_supported_ssn(value: object) -> bool:
    """Check one cell without returning or retaining any matched source text."""

    if value is None:
        return False
    if isinstance(value, bool):
        return False
    text = str(value).strip()
    if _SSN_NUMBER_PATTERN.fullmatch(text) and len(text) < 9:
        text = text.zfill(9)
    return _SSN_PATTERN.search(text) is not None


def scan_ssn_frame(
    frame: object, *, ignored_columns: Sequence[str] = ()
) -> dict[str, int]:
    """Count matching cells by column; no matched values leave this function."""

    columns = getattr(frame, "columns", ())
    ignored = set(ignored_columns)
    findings: dict[str, int] = {}
    for column in columns:
        if str(column) in ignored:
            continue
        values = frame.get_column(column).to_list()
        count = sum(1 for value in values if contains_supported_ssn(value))
        if count:
            findings[str(column)] = count
    return findings


def action_lookup(actions: Sequence[GuardrailAction]) -> dict[tuple[str, str], GuardrailActionName]:
    return {(action.detector, action.column): action.action for action in actions}


__all__ = [
    "GuardrailAction",
    "GuardrailActionName",
    "GuardrailDetector",
    "GuardrailFinding",
    "action_lookup",
    "configured_marker_values",
    "contains_supported_ssn",
    "guardrail_scan_matches_source",
    "guardrail_source_key",
    "matching_metadata_markers",
    "parse_guardrail_action_values",
    "parse_guardrail_scan_result",
    "scan_ssn_frame",
]

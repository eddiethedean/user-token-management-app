"""Streaming, conservative type inference for CSV source files."""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

MAX_CSV_DECIMAL_PRECISION = 38
_INTEGER_PATTERN = re.compile(r"^[+-]?\d+$")
_DECIMAL_PATTERN = re.compile(r"^[+-]?(?:(?:\d+\.\d*|\d*\.\d+)(?:[eE][+-]?\d+)?|\d+[eE][+-]?\d+)$")
_PADDED_NUMBER_PATTERN = re.compile(r"^[+-]?0\d+(?:\.|[eE])")


@dataclass(frozen=True)
class CsvColumnProfile:
    name: str
    inferred_type: str
    populated: int
    nulls: int
    example: str
    decimal_precision: int = 0
    decimal_scale: int = 0
    timezone_aware: bool = False


def csv_dialect(sample: str):
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return csv.excel


def csv_headers(raw_headers: list[str], *, max_columns: int = 200) -> list[str]:
    headers = [header.strip() for header in raw_headers]
    if not headers or any(not header for header in headers):
        raise ValueError("Every CSV column must have a name.")
    if len(headers) > max_columns:
        raise ValueError(f"CSV files may contain at most {max_columns} columns.")
    if any(len(header) > 128 for header in headers):
        raise ValueError("CSV column names must be 128 characters or fewer.")
    if len({header.casefold() for header in headers}) != len(headers):
        raise ValueError("CSV column names must be unique.")
    return headers


def profile_csv_rows(
    headers: list[str],
    rows: Iterable[list[str]],
    *,
    max_cell_characters: int = 131_072,
) -> tuple[int, tuple[CsvColumnProfile, ...]]:
    type_sets: list[set[str]] = [set() for _ in headers]
    populated = [0 for _ in headers]
    nulls = [0 for _ in headers]
    examples = ["" for _ in headers]
    integer_digits = [0 for _ in headers]
    decimal_scales = [0 for _ in headers]
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
                if len(value) > max_cell_characters:
                    raise ValueError("A CSV cell exceeds the 128 KB limit.")
                if not value:
                    nulls[index] += 1
                    continue
                populated[index] += 1
                value_type = _value_type(value)
                if value_type in {"integer", "decimal"}:
                    try:
                        parts = Decimal(value).as_tuple()
                    except InvalidOperation:
                        value_type = "text"
                    else:
                        exponent = int(parts.exponent)
                        integer_digits[index] = max(
                            integer_digits[index], max(len(parts.digits) + exponent, 0)
                        )
                        decimal_scales[index] = max(decimal_scales[index], max(-exponent, 0))
                type_sets[index].add(value_type)
                if not examples[index]:
                    examples[index] = value[:80]
    except csv.Error as exc:
        raise ValueError("The CSV could not be parsed consistently.") from exc
    columns = tuple(
        _column_profile(
            header,
            type_sets[index],
            populated[index],
            nulls[index],
            examples[index],
            integer_digits[index],
            decimal_scales[index],
        )
        for index, header in enumerate(headers)
    )
    return row_count, columns


def _value_type(value: str) -> str:
    lowered = value.casefold()
    if lowered in {"true", "false"}:
        return "boolean"
    integer_value = value.lstrip("+-")
    if _INTEGER_PATTERN.fullmatch(value) and not (
        len(integer_value) > 1 and integer_value.startswith("0")
    ):
        if len(integer_value) > MAX_CSV_DECIMAL_PRECISION:
            return "text"
        if not -(2**63) <= int(value) < 2**63:
            return "decimal"
        return "integer"
    if _DECIMAL_PATTERN.fullmatch(value):
        if _PADDED_NUMBER_PATTERN.match(value):
            return "text"
        return "decimal"
    try:
        if "t" in lowered or " " in value:
            parsed_datetime = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return "datetime_tz" if parsed_datetime.tzinfo is not None else "datetime"
        date.fromisoformat(value)
        return "date"
    except ValueError:
        if ":" not in value:
            return "text"
        try:
            parsed_time = time.fromisoformat(value)
            return "time" if parsed_time.tzinfo is None else "text"
        except ValueError:
            return "text"


def _merge_types(types: set[str]) -> str:
    if not types:
        return "empty"
    if types == {"datetime_tz"}:
        return "datetime"
    if len(types) == 1:
        return next(iter(types))
    if types <= {"integer", "decimal"}:
        return "decimal"
    if types <= {"date", "datetime"}:
        return "datetime"
    return "text"


def _column_profile(
    name: str,
    types: set[str],
    populated: int,
    nulls: int,
    example: str,
    integer_digits: int,
    decimal_scale: int,
) -> CsvColumnProfile:
    inferred_type = _merge_types(types)
    if inferred_type != "decimal":
        return CsvColumnProfile(
            name=name,
            inferred_type=inferred_type,
            populated=populated,
            nulls=nulls,
            example=example,
            timezone_aware=types == {"datetime_tz"},
        )
    precision = max(integer_digits + decimal_scale, decimal_scale, 1)
    if precision > MAX_CSV_DECIMAL_PRECISION:
        raise ValueError(f"CSV decimal precision cannot exceed {MAX_CSV_DECIMAL_PRECISION} digits.")
    return CsvColumnProfile(
        name=name,
        inferred_type=inferred_type,
        populated=populated,
        nulls=nulls,
        example=example,
        decimal_precision=precision,
        decimal_scale=decimal_scale,
    )

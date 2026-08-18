"""Semblance custom links that bind responses to sanitized provider fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from semblance import register_link

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "providers"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FromJsonFixture:
    """Override an output field from a sanitized JSON fixture.

    Semblance treats a ``None`` resolve result as “no override”, so missing keys
    use ``default`` (empty string by default) instead of ``None``.
    """

    def __init__(self, filename: str, key: str | None = None, *, default: object = ""):
        self.filename = filename
        self.key = key
        self.default = default

    def resolve(self, input_data: dict[str, Any], rng: Any) -> object:
        del input_data, rng
        payload = load_fixture(self.filename)
        if self.key is None:
            return payload
        value = payload.get(self.key)
        return self.default if value is None else value


class FromNestedFixture:
    """Walk a JSON fixture by keys; list values resolve to the first element."""

    def __init__(self, filename: str, *keys: str, default: object = ""):
        self.filename = filename
        self.keys = keys
        self.default = default

    def resolve(self, input_data: dict[str, Any], rng: Any) -> object:
        del input_data, rng
        value: Any = load_fixture(self.filename)
        for key in self.keys:
            if isinstance(value, list):
                value = value[0] if value else None
            if isinstance(value, dict):
                value = value.get(key)
        return self.default if value is None else value


class FromConstant:
    def __init__(self, value: object):
        self.value = value

    def resolve(self, input_data: dict[str, Any], rng: Any) -> object:
        del input_data, rng
        return self.value


class FromFoundryFileList:
    """Serve the frozen Foundry list fixture, with pageToken selecting page 2."""

    def __init__(self, field: str):
        self.field = field

    def resolve(self, input_data: dict[str, Any], rng: Any) -> object:
        del rng
        if input_data.get("pageToken"):
            payload = load_fixture("foundry_list_files_paginated.json")
            if self.field == "nextPageToken":
                return ""
            return payload.get("data") or []
        payload = load_fixture("foundry_list_files.json")
        if self.field == "nextPageToken":
            return payload.get("nextPageToken") or ""
        return payload.get("data") or []


register_link(FromJsonFixture)
register_link(FromNestedFixture)
register_link(FromConstant)
register_link(FromFoundryFileList)

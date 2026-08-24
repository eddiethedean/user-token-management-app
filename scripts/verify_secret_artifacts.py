"""Fail if representative plan/report fixtures contain secret-shaped fields or values."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "secret-artifacts"
FORBIDDEN_KEYS = {"token", "password", "client_secret", "authorization", "api_key"}
SECRET_VALUE = re.compile(r"(?:bearer\s+|(?:token|password|secret)\s*[=:]\s*)[^\s,}]+", re.I)


def walk(value: object, path: str = "$"):
    if isinstance(value, dict):
        for key, child in value.items():
            if key.casefold() in FORBIDDEN_KEYS:
                raise ValueError(f"forbidden secret field at {path}.{key}")
            yield from walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, f"{path}[{index}]")
    elif isinstance(value, str) and SECRET_VALUE.search(value):
        raise ValueError(f"secret-shaped value at {path}")


def main() -> int:
    for fixture in sorted(FIXTURES.glob("*.json")):
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        list(walk(payload))
        print(f"verified {fixture.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Secret-reference plan/report fixtures stay free of plaintext credential fields."""

from __future__ import annotations

from scripts.verify_secret_artifacts import main


def test_representative_secret_artifacts_are_secret_free() -> None:
    assert main() == 0

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from fastapi.testclient import TestClient

DEMO_ROOT = Path(__file__).resolve().parents[1]


def load_demo_module(name: str, filename: str) -> ModuleType:
    specification = importlib.util.spec_from_file_location(name, DEMO_ROOT / filename)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"Could not load {filename}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def demo_app_module() -> ModuleType:
    return load_demo_module("posit_demo_app", "app.py")


@pytest.fixture(scope="session")
def demo_start_module() -> ModuleType:
    return load_demo_module("posit_demo_start", "start.py")


@pytest.fixture
def client(demo_app_module: ModuleType) -> Iterator[TestClient]:
    with TestClient(demo_app_module.app) as test_client:
        yield test_client

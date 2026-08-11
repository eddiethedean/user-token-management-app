from __future__ import annotations

from app.config import get_settings
from app.dev_trace import dev_trace, is_dev_trace_enabled, is_static_scope_path


def test_dev_trace_off_by_default_under_pytest() -> None:
    assert is_dev_trace_enabled() is False
    # Must not raise when tracing is off.
    dev_trace("should-be-silent", path="/login")


def test_dev_trace_force_flag(monkeypatch) -> None:
    monkeypatch.setenv("ACCESS_REGISTRY_DEV_TRACE", "1")
    assert is_dev_trace_enabled() is True
    monkeypatch.setenv("ACCESS_REGISTRY_DEV_TRACE", "0")
    assert is_dev_trace_enabled() is False


def test_dev_trace_respects_app_env_when_forced_off_cleared(monkeypatch) -> None:
    monkeypatch.delenv("ACCESS_REGISTRY_DEV_TRACE", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    assert is_dev_trace_enabled() is False

    monkeypatch.setenv("APP_ENV", "development")
    get_settings.cache_clear()
    assert is_dev_trace_enabled() is True


def test_static_scope_path_detection() -> None:
    assert is_static_scope_path("/assets/theme.css")
    assert is_static_scope_path("/hedron-static/htmx.js")
    assert not is_static_scope_path("/login")
    assert not is_static_scope_path("/s/session/p/1/login")

from __future__ import annotations

from app.server import run_server


def test_run_server_delegates_discovery_and_serving_to_hedron_posit(monkeypatch) -> None:
    captured = {}

    def fake_run_target(target, *, config) -> None:
        captured["target"] = target
        captured["config"] = config

    monkeypatch.setattr("app.server.run_target", fake_run_target)

    run_server(host="127.0.0.1", port=8765, reload=True)

    assert captured["target"] == "app.main:app"
    config = captured["config"]
    assert config.host == "127.0.0.1"
    assert config.port == 8765
    assert config.reload is True
    assert config.allow_external_bind is False

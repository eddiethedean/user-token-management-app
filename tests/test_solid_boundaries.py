"""Focused contracts for the incremental SOLID boundaries."""

from __future__ import annotations

import ast
import asyncio
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from app.application.dto import ActorContext, PipelineSummary
from app.application.identity import LoginInput, LoginResult, LoginUseCase
from app.application.pipelines import PipelineUseCases, SavePipelineInput
from app.bootstrap import compose_application
from app.config import Settings
from app.connectors.base import ProviderCapabilities, RegisteredConnector
from app.connectors.registry import ConnectorRegistry, registry_context, writer_enabled
from app.domain.pipelines import PipelinePolicy
from app.services.pipeline_state import PurePipelineRunStateMachine, RunState


def _settings(tmp_path, name: str):
    from app.config import Settings

    return Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / name}",
        public_base_url="http://testserver",
        data_mover_mode="demo",
        jwt_secret="j" * 40,
        session_pepper="p" * 40,
        csrf_secret="c" * 40,
    )


def test_compositions_own_independent_database_and_connector_registries(tmp_path) -> None:
    first = compose_application(_settings(tmp_path, "first.db"))
    second = compose_application(_settings(tmp_path, "second.db"))
    try:
        assert first.database.engine is not second.database.engine
        assert first.connectors is not second.connectors
        assert {item.provider for item in first.connectors.listed_capabilities()} == {
            "postgres",
            "mss",
            "mcscop",
            "csv",
        }
        first_execution_providers = {
            item.provider for item in first.execution.connectors.listed_capabilities()
        }
        first.connectors.clear()
        assert {
            item.provider for item in first.execution.connectors.listed_capabilities()
        } == first_execution_providers
        assert second.connectors.listed_capabilities()
    finally:
        first.close()
        second.close()


def test_create_app_uses_its_composition_for_database_and_settings(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.models import Base

    settings = _settings(tmp_path, "web.db")
    instance = create_app(settings)
    Base.metadata.create_all(instance.state.composition.database.engine)

    @instance.get("/test-unexpected-feedback", include_in_schema=False)
    def unexpected_feedback():
        raise RuntimeError("synthetic unexpected failure")

    with TestClient(instance) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 200
        response = client.get("/test-unexpected-feedback", headers={"Accept": "text/html"})
        assert response.status_code == 500
        assert "Reference: ref-" in response.text
        assert response.headers["X-Request-ID"] != response.headers["X-Support-Reference"]
    assert Exception in instance.exception_handlers


def test_pipeline_use_case_validates_values_before_store() -> None:
    saved: list[PipelineSummary] = []

    class Store:
        def save_pipeline(self, *, actor, draft):
            result = PipelineSummary(
                "p1",
                draft.name,
                draft.source_provider,
                draft.destination_provider,
                draft.write_mode,
            )
            saved.append(result)
            return result

        def enqueue_pipeline(self, **kwargs):
            raise AssertionError("not part of this test")

    policy = PipelinePolicy(
        route_allowed=lambda source, destination: (
            source == "postgres" and destination == "postgres"
        ),
        writer_enabled=lambda provider: provider == "postgres",
        write_modes_for=lambda provider: ("append", "replace"),
    )
    result = PipelineUseCases(Store(), policy).save(
        actor=ActorContext("user-1"),
        command=SavePipelineInput(
            name="  Daily   copy ",
            source_provider="POSTGRES",
            destination_provider="postgres",
            write_mode="APPEND",
            available_providers=frozenset({"postgres"}),
        ),
    )
    assert result.name == "Daily copy"
    assert saved[0].write_mode == "append"


def test_identity_use_case_delegates_to_an_injected_gateway() -> None:
    class Gateway:
        def authenticate_password(self, input, actor):
            assert input.email == "person@example.gov"
            assert actor.request_id == "request-1"
            return LoginResult(user_id="user-1")

    result = LoginUseCase(Gateway()).execute(
        input=LoginInput("person@example.gov", "a-password"),
        actor=ActorContext("anonymous", request_id="request-1"),
    )
    assert result.user_id == "user-1"


def test_pure_run_state_machine_has_no_orm_dependency() -> None:
    clock_value = datetime.now(UTC).replace(tzinfo=None)
    machine = PurePipelineRunStateMachine(clock=lambda: clock_value)
    run = RunState(status="queued", lease_token="lease")
    machine.transition(run, "validating", lease_token="lease")
    assert run.stage == "authenticate"
    assert run.updated_at is clock_value


def test_provider_writer_policy_comes_from_registered_capability() -> None:
    from types import SimpleNamespace

    settings = SimpleNamespace(
        is_demo_mode=False,
        enable_example_writer=True,
    )
    registry = ConnectorRegistry()
    connector = SimpleNamespace(
        capabilities=ProviderCapabilities(
            provider="example",
            label="Example",
            technology="Test",
            mark="EX",
            source=True,
            destination=True,
            object_model="table",
            write_modes=("append",),
            namespaces_label="Schema",
            objects_label="Table",
            writer_setting="enable_example_writer",
        )
    )
    with registry_context(registry, cast(Settings, settings)):
        registry.register(cast(Callable[[], RegisteredConnector], lambda: connector))
        assert writer_enabled("example") is True
        settings.enable_example_writer = False
        assert writer_enabled("example") is False


def test_owned_registry_rejects_invalid_writer_setting_at_registration(tmp_path) -> None:
    from types import SimpleNamespace

    settings = _settings(tmp_path, "invalid-provider.db")
    registry = ConnectorRegistry(settings=settings)
    connector = SimpleNamespace(
        capabilities=ProviderCapabilities(
            provider="invalid",
            label="Invalid",
            technology="Test",
            mark="IV",
            source=False,
            destination=True,
            object_model="table",
            write_modes=("append",),
            namespaces_label="Schema",
            objects_label="Table",
            writer_setting="does_not_exist",
        )
    )
    with pytest.raises(ValueError, match="missing writer setting"):
        registry.register(cast(Callable[[], RegisteredConnector], lambda: connector))


def test_permissive_registry_validates_writer_metadata_when_published(tmp_path) -> None:
    """Legacy registration must not bypass validation at runtime publication."""

    settings = _settings(tmp_path, "published-invalid-provider.db")
    registry = ConnectorRegistry()
    connector = SimpleNamespace(
        capabilities=ProviderCapabilities(
            provider="invalid",
            label="Invalid",
            technology="Test",
            mark="IV",
            source=False,
            destination=True,
            object_model="table",
            write_modes=("append",),
            namespaces_label="Schema",
            objects_label="Table",
            writer_setting="does_not_exist",
        )
    )
    registry.register(cast(Callable[[], RegisteredConnector], lambda: connector))

    with pytest.raises(ValueError, match="missing writer setting"):
        registry.snapshot(settings)


def test_execution_runtime_binds_every_owned_dependency(tmp_path, monkeypatch) -> None:
    from app.connectors.registry import connector_settings, current_registry
    from app.database import current_session_factory
    from app.services import pipeline_tasks

    composition = compose_application(_settings(tmp_path, "runtime.db"))
    observed: dict[str, object] = {}

    def process_one(db, settings, **kwargs):
        observed["session_factory"] = current_session_factory()
        observed["registry"] = current_registry()
        observed["settings"] = connector_settings()
        return False

    monkeypatch.setattr("app.worker.process_one", process_one)
    try:
        assert (
            pipeline_tasks.process_pending_pipeline_run_background(composition.execution) is False
        )
        assert observed == {
            "session_factory": composition.execution.sessions,
            "registry": composition.execution.connectors,
            "settings": composition.settings,
        }
    finally:
        composition.close()


def test_security_connection_check_uses_the_request_runtime_database(tmp_path, monkeypatch) -> None:
    from app.models import Base, User
    from app.ui.routes.security import _test_user_connection_in_thread

    first = compose_application(_settings(tmp_path, "security-first.db"))
    second = compose_application(_settings(tmp_path, "security-second.db"))
    Base.metadata.create_all(first.database.engine)
    Base.metadata.create_all(second.database.engine)
    with first.database.session() as db:
        db.add(User(id="first-user", email="first@example.gov", email_original="first@example.gov"))
        db.commit()
    with second.database.session() as db:
        db.add(
            User(id="second-user", email="second@example.gov", email_original="second@example.gov")
        )
        db.commit()

    observed = {}

    def connection_check(db, **kwargs):
        observed["request"] = kwargs["request"]
        return db.get(User, kwargs["user"].id).email

    monkeypatch.setattr("app.ui.routes.security.test_user_connection", connection_check)
    try:
        result = asyncio.run(
            first.execution.run_owned_sync(
                _test_user_connection_in_thread,
                first.settings,
                "first-user",
                "postgres",
                "request-1",
                "ref-1",
            )
        )
        assert result == "first@example.gov"
        assert observed["request"].request_id == "request-1"
        assert observed["request"].reference_id == "ref-1"
    finally:
        first.close()
        second.close()


def test_background_runtime_rejects_partial_session_override() -> None:
    from app.config import get_settings
    from app.services.pipeline_tasks import run_background_runtime

    with pytest.raises(ValueError, match="complete ExecutionRuntime"):
        import asyncio
        from contextlib import nullcontext

        asyncio.run(
            run_background_runtime(
                get_settings(), threading.Event(), session_factory=lambda: nullcontext()
            )
        )


def test_background_runtime_rejects_any_partial_dependency_set() -> None:
    from app.config import get_settings
    from app.services.pipeline_tasks import run_background_runtime

    with pytest.raises(ValueError, match="complete ExecutionRuntime"):
        import asyncio

        asyncio.run(
            run_background_runtime(
                get_settings(),
                threading.Event(),
                registry=ConnectorRegistry(),
            )
        )


def test_background_runtime_rejects_conflicting_settings_with_runtime(tmp_path) -> None:
    from app.services.pipeline_tasks import run_background_runtime

    composition = compose_application(_settings(tmp_path, "runtime-conflict.db"))
    other_settings = _settings(tmp_path, "other-runtime.db")
    try:
        with pytest.raises(ValueError, match="independent resource overrides"):
            import asyncio

            asyncio.run(
                run_background_runtime(
                    settings=other_settings,
                    runtime=composition.execution,
                )
            )
    finally:
        composition.close()


def test_owned_registry_injects_its_settings_without_masking_factory_errors(tmp_path) -> None:
    from types import SimpleNamespace

    settings = _settings(tmp_path, "extension.db")
    registry = ConnectorRegistry(settings=settings)
    observed: list[object] = []
    connector = SimpleNamespace(
        capabilities=ProviderCapabilities(
            provider="extension",
            label="Extension",
            technology="Test",
            mark="EX",
            source=True,
            destination=False,
            object_model="table",
            write_modes=(),
            namespaces_label="Schema",
            objects_label="Table",
        ),
        received_settings=None,
    )

    def factory(*, settings=None):
        observed.append(settings)
        connector.received_settings = settings
        return connector

    registry.register(cast(Callable[[], RegisteredConnector], factory))
    resolved = cast(SimpleNamespace, registry.connector_for("extension"))
    assert resolved.received_settings is settings
    assert observed == [settings, settings]

    calls = 0

    def broken_factory():
        nonlocal calls
        calls += 1
        raise TypeError("factory body failure")

    with pytest.raises(TypeError, match="factory body failure"):
        registry.register(broken_factory)
    assert calls == 1


def test_catalog_factory_converts_request_facts_to_metadata(tmp_path) -> None:
    from fastapi import Request

    from app.infrastructure.catalog_factory import build_user_catalog

    settings = _settings(tmp_path, "catalog-metadata.db")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/pipeline",
            "headers": [],
            "client": ("127.0.0.1", 5000),
        }
    )
    request.state.request_id = "request-123"
    catalog = build_user_catalog(object(), settings, object(), request)
    from app.application.ports import RequestMetadata
    from app.infrastructure.security.credentials import SqlAlchemyCredentialResolver

    resolver = cast(SqlAlchemyCredentialResolver, catalog.credential_resolver)
    metadata = cast(RequestMetadata, resolver.request)
    assert metadata.request_id == "request-123"
    assert metadata.source_ip == "127.0.0.1"


def test_domain_package_does_not_import_framework_or_persistence_modules() -> None:
    root = Path(__file__).parents[1] / "app" / "domain"
    forbidden = ("fastapi", "starlette", "sqlalchemy", "app.models", "app.database")
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        modules = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not any(module.startswith(forbidden) for module in modules), path

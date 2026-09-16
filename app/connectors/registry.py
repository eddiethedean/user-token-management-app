"""Provider ID to connector factory and capability lookup."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from inspect import Parameter, signature
from typing import TYPE_CHECKING, Literal, TypeVar, cast

from app.connectors.base import (
    CatalogBrowser,
    CatalogReader,
    ConnectionTester,
    DatasetProvisioner,
    DestinationWriter,
    ObjectSchemaInspector,
    ProviderCapabilities,
    RegisteredConnector,
    RowCounter,
    SourceReader,
)
from app.connectors.errors import ConnectorError, TransferErrorCode

if TYPE_CHECKING:
    from app.config import Settings

ConnectorFactory = Callable[[], RegisteredConnector]
RoleT = TypeVar("RoleT")


def _settings_facts(settings: Settings | None) -> tuple[tuple[str, str], ...]:
    if settings is None:
        return ()
    values = getattr(settings, "model_dump", lambda: vars(settings))()
    return tuple(sorted((key, repr(value)) for key, value in values.items()))


def settings_facts(settings: Settings | None) -> tuple[tuple[str, str], ...]:
    """Return stable value facts for a settings authority."""

    return _settings_facts(settings)


@dataclass(frozen=True)
class ProviderSpecification:
    """Registered provider facts used by application policy and composition."""

    capabilities: ProviderCapabilities
    factory: ConnectorFactory
    raw_factory: ConnectorFactory | None = None

    @property
    def name(self) -> str:
        return self.capabilities.provider

    @property
    def writer_setting(self) -> str | None:
        return self.capabilities.writer_setting


class ConnectorRegistry:
    """Replaceable connector registry for application and test composition."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._specifications: dict[str, ProviderSpecification] = {}
        self._settings = settings
        self._owned = settings is not None
        self._revision = 0
        self._sealed = False

    def register(self, factory: ConnectorFactory) -> ConnectorFactory:
        if self._sealed:
            raise RuntimeError("A published connector generation is immutable.")
        owner_settings = self._settings or _ACTIVE_SETTINGS.get()

        bound_factory = self._bind_factory(factory, owner_settings)

        connector = bound_factory()
        provider = connector.capabilities.provider.casefold()
        specification = ProviderSpecification(connector.capabilities, bound_factory, factory)
        # Only an app-owned registry can validate extension metadata at
        # registration time.  The process-global compatibility registry is
        # intentionally permissive until a composed settings object is bound.
        if self._settings is not None:
            candidate = dict(self._specifications)
            candidate[provider] = specification
            self._validate_specifications(candidate, self._settings)
        self._specifications[provider] = specification
        self._revision += 1
        return factory

    def _bind_factory(
        self, factory: ConnectorFactory, owner_settings: Settings | None
    ) -> ConnectorFactory:
        def bound_factory() -> RegisteredConnector:
            if owner_settings is None:
                return factory()
            with registry_context(self, owner_settings):
                if _factory_accepts_settings(factory):
                    return factory(settings=owner_settings)  # type: ignore[call-arg]
                return factory()

        return bound_factory

    def connector_for(self, provider: str) -> RegisteredConnector:
        specification = self._specifications.get(provider.casefold())
        if specification is None:
            raise ConnectorError(
                code=TransferErrorCode.INTERNAL_ERROR,
                summary="Select a supported connection provider.",
                retryable=False,
            )
        return specification.factory()

    def _role_for(
        self,
        provider: str,
        role: type[RoleT],
        label: str,
        *,
        capabilities: tuple[
            Literal[
                "source", "destination", "dataset_creation", "schema_inspection", "exact_row_counts"
            ],
            ...,
        ] = (),
    ) -> RoleT:
        provider_capabilities = self.capabilities_for(provider)
        if any(not getattr(provider_capabilities, capability) for capability in capabilities):
            raise ConnectorError(
                code=TransferErrorCode.INTERNAL_ERROR,
                summary=f"The selected provider does not support {label}.",
                retryable=False,
            )
        connector = self.connector_for(provider)
        if not isinstance(connector, role):
            raise ConnectorError(
                code=TransferErrorCode.INTERNAL_ERROR,
                summary=f"The selected provider does not support {label}.",
                retryable=False,
            )
        return cast(RoleT, connector)

    def catalog_reader_for(self, provider: str) -> CatalogReader:
        return self._role_for(
            provider,
            CatalogReader,
            "catalog browsing",
            capabilities=("schema_inspection", "exact_row_counts"),
        )

    def catalog_browser_for(self, provider: str) -> CatalogBrowser:
        return self._role_for(provider, CatalogBrowser, "catalog browsing")

    def object_schema_inspector_for(self, provider: str) -> ObjectSchemaInspector:
        return self._role_for(
            provider,
            ObjectSchemaInspector,
            "schema inspection",
            capabilities=("schema_inspection",),
        )

    def row_counter_for(self, provider: str) -> RowCounter:
        return self._role_for(
            provider,
            RowCounter,
            "row counting",
            capabilities=("exact_row_counts",),
        )

    def connection_tester_for(self, provider: str) -> ConnectionTester:
        return self._role_for(provider, ConnectionTester, "connection testing")

    def source_reader_for(self, provider: str) -> SourceReader:
        return self._role_for(
            provider,
            SourceReader,
            "source extraction",
            capabilities=("source",),
        )

    def destination_writer_for(self, provider: str) -> DestinationWriter:
        return self._role_for(
            provider,
            DestinationWriter,
            "destination writes",
            capabilities=("destination",),
        )

    def dataset_provisioner_for(self, provider: str) -> DatasetProvisioner:
        return cast(
            DatasetProvisioner,
            self._role_for(
                provider,
                DatasetProvisioner,
                "dataset creation",
                capabilities=("dataset_creation",),
            ),
        )

    def capabilities_for(self, provider: str) -> ProviderCapabilities:
        try:
            return self._specifications[provider.casefold()].capabilities
        except KeyError as exc:
            raise ConnectorError(
                code=TransferErrorCode.INTERNAL_ERROR,
                summary="Select a supported connection provider.",
                retryable=False,
            ) from exc

    def listed_capabilities(
        self,
        *,
        sources: bool | None = None,
        destinations: bool | None = None,
    ) -> list[ProviderCapabilities]:
        items = [item.capabilities for item in self._specifications.values()]
        if sources is not None:
            items = [item for item in items if item.source is sources]
        if destinations is not None:
            items = [item for item in items if item.destination is destinations]
        return items

    def clear(self) -> None:
        if self._sealed:
            raise RuntimeError("A published connector generation is immutable.")
        self._specifications.clear()
        self._revision += 1

    @property
    def sealed(self) -> bool:
        """Whether this registry is a published, immutable generation."""

        return self._sealed

    @property
    def revision(self) -> int:
        """Monotonic revision for in-place registry changes."""

        return self._revision

    def configuration_facts(self) -> tuple[tuple[str, str, int], ...]:
        """Return stable identity facts for the registered provider factories."""

        return tuple(
            (
                specification.name.casefold(),
                repr(specification.capabilities),
                id(specification.raw_factory or specification.factory),
            )
            for specification in self.specifications()
        )

    def settings_facts(self) -> tuple[tuple[str, str], ...]:
        """Return the captured settings authority for this registry generation."""

        return _settings_facts(self._settings)

    def snapshot(self, settings: Settings | None = None) -> ConnectorRegistry:
        """Create an app-owned registry detached from future source mutations."""

        captured_settings = deepcopy(settings if settings is not None else self._settings)
        if (
            self._sealed
            and settings is not None
            and _settings_facts(self._settings) != _settings_facts(settings)
        ):
            raise ValueError("A published connector generation is bound to different settings.")
        snapshot = ConnectorRegistry(settings=captured_settings)
        for specification in self.specifications():
            factory = specification.raw_factory or specification.factory
            bound_factory = snapshot._bind_factory(factory, captured_settings)
            snapshot._specifications[specification.name.casefold()] = ProviderSpecification(
                specification.capabilities,
                bound_factory,
                factory,
            )
            snapshot._revision += 1
        if captured_settings is not None:
            snapshot._validate_specifications(snapshot._specifications, captured_settings)
        snapshot._sealed = True
        return snapshot

    def specification_for(self, provider: str) -> ProviderSpecification:
        try:
            return self._specifications[provider.casefold()]
        except KeyError as exc:
            raise ConnectorError(
                code=TransferErrorCode.INTERNAL_ERROR,
                summary="Select a supported connection provider.",
                retryable=False,
            ) from exc

    def specifications(self) -> tuple[ProviderSpecification, ...]:
        return tuple(self._specifications.values())

    def validate_writer_settings(self, settings: Settings) -> None:
        """Validate declared writer flags against the composed settings object."""

        self._validate_specifications(self._specifications, settings)

    @staticmethod
    def _validate_specifications(
        specifications: dict[str, ProviderSpecification], settings: Settings
    ) -> None:
        fields = getattr(type(settings), "model_fields", None)
        if fields is None:
            fields = getattr(settings, "model_fields", {})
        for specification in specifications.values():
            name = specification.writer_setting
            if name is None:
                continue
            if not name or name not in fields or not hasattr(settings, name):
                raise ValueError(
                    f"Provider {specification.name!r} declares missing writer setting {name!r}."
                )
            field = fields[name]
            annotation = getattr(field, "annotation", None)
            if annotation is not bool:
                raise ValueError(
                    f"Provider {specification.name!r} writer setting {name!r} must be a boolean field."
                )
            value = getattr(settings, name)
            if not isinstance(value, bool):
                raise ValueError(
                    f"Provider {specification.name!r} writer setting {name!r} must be boolean."
                )


_DEFAULT_REGISTRY = ConnectorRegistry()
_ACTIVE_REGISTRY: ContextVar[ConnectorRegistry | None] = ContextVar(
    "access_registry_connector_registry", default=None
)
_ACTIVE_SETTINGS: ContextVar[Settings | None] = ContextVar(
    "access_registry_connector_settings", default=None
)


def current_registry() -> ConnectorRegistry:
    """Return the registry for the current composition context."""

    return _ACTIVE_REGISTRY.get() or _DEFAULT_REGISTRY


def legacy_registry() -> ConnectorRegistry:
    """Return the process-global registry without consulting task context."""

    return _DEFAULT_REGISTRY


def legacy_registry_settings() -> Settings | None:
    """Return settings captured by the process-global registry, if any."""

    return _DEFAULT_REGISTRY._settings


def bind_registry(registry: ConnectorRegistry | None, settings: Settings | None = None):
    """Bind an app-owned registry for the current request/task context."""

    token = (_ACTIVE_REGISTRY.set(registry), _ACTIVE_SETTINGS.set(settings))
    return token


def unbind_registry(token) -> None:
    registry_token, settings_token = token
    _ACTIVE_SETTINGS.reset(settings_token)
    _ACTIVE_REGISTRY.reset(registry_token)


@contextmanager
def registry_context(registry: ConnectorRegistry, settings: Settings | None = None):
    """Temporarily route connector decorators and lookups to ``registry``."""

    token = _ACTIVE_REGISTRY.set(registry)
    settings_token = _ACTIVE_SETTINGS.set(settings)
    try:
        yield registry
    finally:
        _ACTIVE_SETTINGS.reset(settings_token)
        _ACTIVE_REGISTRY.reset(token)


def connector_settings():
    """Return composition settings while a provider is being instantiated."""

    from app.config import get_settings

    return _ACTIVE_SETTINGS.get() or get_settings()


def register_connector(factory: ConnectorFactory) -> ConnectorFactory:
    return current_registry().register(factory)


def connector_for(provider: str) -> RegisteredConnector:
    return current_registry().connector_for(provider)


def catalog_reader_for(provider: str) -> CatalogReader:
    return current_registry().catalog_reader_for(provider)


def catalog_browser_for(provider: str) -> CatalogBrowser:
    return current_registry().catalog_browser_for(provider)


def object_schema_inspector_for(provider: str) -> ObjectSchemaInspector:
    return current_registry().object_schema_inspector_for(provider)


def row_counter_for(provider: str) -> RowCounter:
    return current_registry().row_counter_for(provider)


def connection_tester_for(provider: str) -> ConnectionTester:
    return current_registry().connection_tester_for(provider)


def source_reader_for(provider: str) -> SourceReader:
    return current_registry().source_reader_for(provider)


def destination_writer_for(provider: str) -> DestinationWriter:
    return current_registry().destination_writer_for(provider)


def capabilities_for(provider: str) -> ProviderCapabilities:
    return current_registry().capabilities_for(provider)


def dataset_provisioner_for(provider: str) -> DatasetProvisioner:
    """Resolve a provider that explicitly supports dataset provisioning."""

    return current_registry().dataset_provisioner_for(provider)


def listed_capabilities(*, sources: bool | None = None, destinations: bool | None = None):
    return current_registry().listed_capabilities(sources=sources, destinations=destinations)


def provider_specification_for(provider: str) -> ProviderSpecification:
    return current_registry().specification_for(provider)


def provider_specifications() -> tuple[ProviderSpecification, ...]:
    return current_registry().specifications()


def route_allowed(source_provider: str, destination_provider: str) -> bool:
    """Return whether the registered connector roles can form this route.

    Route compatibility is derived from connector capabilities so adding a
    provider does not require a second, easy-to-forget global route matrix.
    Operational writer flags remain a separate gate for destinations.
    """

    try:
        source = capabilities_for(source_provider)
        destination = capabilities_for(destination_provider)
    except ConnectorError:
        return False
    return bool(source.source and destination.destination)


def writer_enabled(destination_provider: str, *, settings: Settings | None = None) -> bool:
    capabilities = capabilities_for(destination_provider)
    if not capabilities.destination:
        return False
    if settings is None:
        from app.config import get_settings

        settings = _ACTIVE_SETTINGS.get() or get_settings()
    if settings.is_demo_mode:
        return capabilities.writer_enabled
    setting_name = current_registry().specification_for(destination_provider).writer_setting
    if setting_name is None:
        return capabilities.writer_enabled
    configured = getattr(settings, setting_name, None)
    return configured if isinstance(configured, bool) else False


def load_builtin_connectors(
    *, demo: bool, registry: ConnectorRegistry | None = None, settings: Settings | None = None
) -> ConnectorRegistry:
    """Register fake or real connectors. Safe to call more than once."""
    target = registry or current_registry()
    if target._sealed:
        raise RuntimeError("A published connector generation is immutable.")
    if settings is not None:
        if target._owned and target._settings is not settings:
            raise ValueError("An owned connector registry cannot be rebound to other settings.")
        target._settings = settings
        if target is not _DEFAULT_REGISTRY:
            target._owned = True
    target.clear()
    with registry_context(target, settings):
        if demo:
            from app.connectors import fake as _fake

            _fake.register()
            if settings is not None:
                target.validate_writer_settings(settings)
            return target
        from app.connectors import csv_source, mcscop, mss, postgres

        postgres.register()
        mss.register()
        mcscop.register()
        csv_source.register()
        if settings is not None:
            target.validate_writer_settings(settings)
    return target


def _factory_accepts_settings(factory: ConnectorFactory) -> bool:
    """Detect settings injection without swallowing factory-body exceptions."""

    try:
        parameters = signature(factory).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == "settings"
        and parameter.kind in {Parameter.POSITIONAL_OR_KEYWORD, Parameter.KEYWORD_ONLY}
        for parameter in parameters
    ) or any(parameter.kind is Parameter.VAR_KEYWORD for parameter in parameters)

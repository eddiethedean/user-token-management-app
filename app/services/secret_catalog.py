"""Provider credential catalog, independent of storage and runtime checks."""

from __future__ import annotations

from collections.abc import Iterable

from app.services.secrets_types import CredentialField, SecretProvider


class SecretCatalog:
    """Immutable provider lookup used by UI, storage, and runtime adapters."""

    def __init__(self, providers: Iterable[SecretProvider]) -> None:
        self._providers = tuple(providers)
        self._by_name = {provider.name.casefold(): provider for provider in self._providers}

    @property
    def providers(self) -> tuple[SecretProvider, ...]:
        return self._providers

    def require(self, provider: str) -> SecretProvider:
        specification = self._by_name.get(provider.casefold())
        if specification is None:
            raise ValueError("Select a supported connection provider.")
        return specification


SECRET_PROVIDERS = (
    SecretProvider(
        "mss",
        "MSS",
        "MSS",
        "MSS_API_TOKEN",
        (
            CredentialField(
                "endpoint", "API endpoint", "https://mss.example", input_type="url", required=True
            ),
            CredentialField(
                "token",
                "API token",
                "Paste MSS API token",
                input_type="password",
                autocomplete="new-password",
                required=True,
            ),
            CredentialField("dataset_rid", "Default dataset RID", "ri.foundry.main.dataset..."),
            CredentialField("branch", "Default branch", "master", default="master"),
            CredentialField(
                "ca_profile",
                "TLS CA profile",
                "system",
                default="system",
                options=("system", "nipr"),
            ),
        ),
    ),
    SecretProvider(
        "mcscop",
        "MCS-COP",
        "MCS",
        "MCSCOP_API_TOKEN",
        (
            CredentialField(
                "endpoint",
                "API endpoint",
                "https://mcscop.example",
                input_type="url",
                required=True,
            ),
            CredentialField(
                "token",
                "API token",
                "Paste MCS-COP API token",
                input_type="password",
                autocomplete="new-password",
                required=True,
            ),
            CredentialField("dataset_rid", "Default dataset RID", "ri.foundry.main.dataset..."),
            CredentialField("branch", "Default branch", "master", default="master"),
            CredentialField(
                "ca_profile",
                "TLS CA profile",
                "system",
                default="system",
                options=("system", "nipr"),
            ),
        ),
    ),
    SecretProvider(
        "postgres",
        "PostgreSQL",
        "PG",
        "DATABASE_URL",
        (
            CredentialField("host", "Host", "db.example.internal", required=True),
            CredentialField("port", "Port", "5432", required=True, default="5432"),
            CredentialField("database", "Database", "analytics", required=True),
            CredentialField(
                "username",
                "Username",
                "data_mover_service",
                autocomplete="username",
                required=True,
            ),
            CredentialField(
                "password",
                "Password",
                "Enter database password",
                input_type="password",
                autocomplete="new-password",
                required=True,
            ),
            CredentialField(
                "sslmode",
                "SSL mode",
                "require",
                required=True,
                default="require",
                options=("require", "verify-ca", "verify-full"),
            ),
            CredentialField("connect_timeout", "Connect timeout (seconds)", "10", default="10"),
            CredentialField(
                "application_name", "Application name", "data-mover", default="data-mover"
            ),
        ),
    ),
)

SECRET_CATALOG = SecretCatalog(SECRET_PROVIDERS)
SECRET_PROVIDER_MAP = {provider.name: provider for provider in SECRET_PROVIDERS}

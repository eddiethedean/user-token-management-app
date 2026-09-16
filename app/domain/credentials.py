"""Framework-neutral credential specifications and lookup policy."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class CredentialField:
    name: str
    label: str
    placeholder: str
    input_type: str = "text"
    autocomplete: str = "off"
    required: bool = False
    default: str = ""
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class SecretProvider:
    name: str
    label: str
    mark: str
    environment_variable: str
    fields: tuple[CredentialField, ...]
    setup_hint: str = ""


class SecretCatalog:
    """Immutable provider catalog owned by the credential domain."""

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

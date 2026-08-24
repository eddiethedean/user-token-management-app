"""Shared immutable types for the secret domain."""

from __future__ import annotations

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

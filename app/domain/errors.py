"""Business failures that do not know about HTTP or persistence."""

from __future__ import annotations


class DomainError(ValueError):
    """A request cannot be represented as a valid domain value."""


class UnsupportedRoute(DomainError):
    """A source/destination capability combination is not valid."""


class UnsupportedWriteMode(DomainError):
    """A destination does not support the requested write mode."""

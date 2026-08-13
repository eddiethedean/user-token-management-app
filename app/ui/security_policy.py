"""Shared Hedron SecurityPolicy for Data Mover.

Data Mover owns CSRF and security headers; Hedron's built-in CSRF and
header middleware stay disabled in both the app factory and fragment renderer.
"""

from __future__ import annotations

from hedron.security.policy import SecurityPolicy


def access_registry_security_policy() -> SecurityPolicy:
    return SecurityPolicy(
        csrf_enabled=False,
        security_headers=False,
        explorer_enabled=False,
        private_authenticated_cache=True,
        content_security_policy=None,
    )

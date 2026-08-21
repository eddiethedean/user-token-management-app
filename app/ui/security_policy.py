"""Shared Hedron SecurityPolicy for Data Mover.

Data Mover owns CSRF and security headers; Hedron's built-in CSRF and
header middleware stay disabled in both the app factory and fragment renderer.
"""

from __future__ import annotations

from hedron.security.policy import SecurityPolicy
from hedron_core.request_budget import RequestBudgetLimits


def access_registry_security_policy() -> SecurityPolicy:
    return SecurityPolicy(
        csrf_enabled=False,
        security_headers=False,
        explorer_enabled=False,
        private_authenticated_cache=True,
        content_security_policy=None,
        # Data Mover owns CSRF and response headers, but still publishes its
        # 0.56 control-plane posture to Hedron diagnostics and integrations.
        control_plane_version=1,
        conformance_profile_version="hedron-security-1",
        intent_required=False,
        posture_strict=False,
        request_budget_limits=RequestBudgetLimits(
            body_bytes=5 * 1024 * 1024,
            response_bytes=10 * 1024 * 1024,
            multipart_parts=16,
            form_fields=256,
            deadline_seconds=120,
        ),
        egress_allow_hosts=frozenset(),
        egress_deny_by_default=True,
    )

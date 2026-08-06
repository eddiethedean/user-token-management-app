"""Access Registry UI fragment builders."""

from __future__ import annotations

from app.ui.partials.admin_audit import (
    _audit_filter_form,
    audit_match_count,
    audit_panel,
    audit_refresh_button,
    audit_results,
    audit_results_body,
    audit_results_error,
    audit_results_lazy,
)
from app.ui.partials.admin_users import (
    invitation_panel,
    user_directory,
    user_match_count,
    user_table,
)
from app.ui.partials.profile import profile_form, profile_identity, profile_response
from app.ui.partials.security import (
    _password_field,
    password_form,
    secret_slot,
    security_activity,
    security_activity_error,
    security_activity_lazy,
    security_activity_refresh,
    security_tabs,
    session_count,
    session_list,
)
from app.ui.partials.shared import (
    _filter_base_path,
    hedron_pagination,
    request_error,
)

__all__ = [
    "_audit_filter_form",
    "_filter_base_path",
    "_password_field",
    "audit_match_count",
    "audit_panel",
    "audit_refresh_button",
    "audit_results",
    "audit_results_body",
    "audit_results_error",
    "audit_results_lazy",
    "hedron_pagination",
    "invitation_panel",
    "password_form",
    "profile_form",
    "profile_identity",
    "profile_response",
    "request_error",
    "secret_slot",
    "security_activity",
    "security_activity_error",
    "security_activity_lazy",
    "security_activity_refresh",
    "security_tabs",
    "session_count",
    "session_list",
    "user_directory",
    "user_match_count",
    "user_table",
]

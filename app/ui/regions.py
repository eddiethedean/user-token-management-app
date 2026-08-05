"""Shared HTMX fragment region allowlists."""

from __future__ import annotations

from hedron import FragmentRegion

MAIN_PANEL = FragmentRegion(
    id="main-panel",
    selector="#main-panel",
    description="Authenticated main content panel",
)
TOAST_HOST = FragmentRegion(
    id="toast-host",
    selector="#toast-host",
    description="Transient toast notifications",
)
SIDE_NAV = FragmentRegion(
    id="side-nav",
    selector="#side-nav",
    description="Account side navigation",
)
DIALOG_HOST = FragmentRegion(
    id="dialog-host",
    selector="#dialog-host",
    description="Modal dialog host",
)
ACCOUNT_SUMMARY = FragmentRegion(
    id="account-summary",
    selector="#account-summary",
    description="Header account summary",
)
PROFILE_IDENTITY = FragmentRegion(
    id="profile-identity",
    selector="#profile-identity",
    description="Profile identity sidebar",
)
PROFILE_FORM = FragmentRegion(
    id="profile-form-region",
    selector="#profile-form-region",
    description="Profile edit form",
)
SESSION_COUNT = FragmentRegion(
    id="session-count",
    selector="#session-count",
    description="Active session count badge",
)
USER_MATCH_COUNT = FragmentRegion(
    id="user-match-count",
    selector="#user-match-count",
    description="Admin directory match count",
)
AUDIT_MATCH_COUNT = FragmentRegion(
    id="audit-match-count",
    selector="#audit-match-count",
    description="Audit log match count",
)
PASSWORD_FORM = FragmentRegion(
    id="password-form-region",
    selector="#password-form-region",
    description="Password change form",
)
SESSION_LIST = FragmentRegion(
    id="session-list",
    selector="#session-list",
    description="Active session list",
)
SECRET_SLOT_ADVANA = FragmentRegion(
    id="secret-slot-advana",
    selector="#secret-slot-advana",
    description="Advana API token slot",
)
SECRET_SLOT_ADE = FragmentRegion(
    id="secret-slot-ade",
    selector="#secret-slot-ade",
    description="ADE API token slot",
)
SECRET_SLOT_MSS = FragmentRegion(
    id="secret-slot-mss",
    selector="#secret-slot-mss",
    description="MSS API token slot",
)
SECRET_SLOT_PREFIX = SECRET_SLOT_ADVANA  # backward-compatible alias
INVITATION_PANEL = FragmentRegion(
    id="invitation-panel",
    selector="#invitation-panel",
    description="Administrator invitation panel",
)
USER_DIRECTORY = FragmentRegion(
    id="user-directory",
    selector="#user-directory",
    description="Administrator user directory",
)
USER_DIRECTORY_BODY = FragmentRegion(
    id="user-directory-body",
    selector="#user-directory-body",
    description="Administrator user directory table body",
)
USER_TABLE = FragmentRegion(
    id="user-table",
    selector="#user-table",
    description="Administrator user table (legacy alias)",
)
AUDIT_RESULTS = FragmentRegion(
    id="audit-results-region",
    selector="#audit-results-region",
    description="Audit log results",
)
AUDIT_RESULTS_BODY = FragmentRegion(
    id="audit-results-body",
    selector="#audit-results-body",
    description="Audit log results table body",
)
SECURITY_ACTIVITY = FragmentRegion(
    id="security-activity",
    selector="#security-activity",
    description="Recent security activity",
)
GLOBAL_FEEDBACK = FragmentRegion(
    id="global-feedback",
    selector="#global-feedback",
    description="Global request feedback",
)

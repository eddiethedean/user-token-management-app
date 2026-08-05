"""Shared HTMX fragment region allowlists."""

from __future__ import annotations

from hedron import FragmentRegion

PROFILE_FORM = FragmentRegion(
    id="profile-form-region",
    selector="#profile-form-region",
    description="Profile edit form",
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
SECRET_SLOT_PREFIX = FragmentRegion(
    id="secret-slot",
    selector="[id^='secret-slot-']",
    description="API token provider slots",
)
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
USER_TABLE = FragmentRegion(
    id="user-table",
    selector="#user-table",
    description="Administrator user table",
)
AUDIT_RESULTS = FragmentRegion(
    id="audit-results-region",
    selector="#audit-results-region",
    description="Audit log results",
)
GLOBAL_FEEDBACK = FragmentRegion(
    id="global-feedback",
    selector="#global-feedback",
    description="Global request feedback",
)

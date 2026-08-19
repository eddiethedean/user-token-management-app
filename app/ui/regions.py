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
SECRET_SLOT_MSS = FragmentRegion(
    id="secret-slot-mss",
    selector="#secret-slot-mss",
    description="MSS connection credential slot",
)
SECRET_SLOT_MCSCOP = FragmentRegion(
    id="secret-slot-mcscop",
    selector="#secret-slot-mcscop",
    description="MCS-COP connection credential slot",
)
SECRET_SLOT_POSTGRES = FragmentRegion(
    id="secret-slot-postgres",
    selector="#secret-slot-postgres",
    description="PostgreSQL connection credential slot",
)
CONNECTION_STATUS_LIST = FragmentRegion(
    id="connection-status-list",
    selector="#connection-status-list",
    description="Remote connection validation status list",
)
CSV_INSPECTION = FragmentRegion(
    id="pipeline-csv-inspection",
    selector="#pipeline-csv-inspection",
    description="Uploaded CSV schema inspection",
)
CSV_UPLOAD_STATE = FragmentRegion(
    id="pipeline-csv-upload-state",
    selector="#pipeline-csv-upload-state",
    description="CSV upload scan status",
)
PIPELINE_SOURCE_SCHEMA_SELECT = FragmentRegion(
    id="pipeline-source-schema-select",
    selector="#pipeline-source-schema-select",
    description="Pipeline source schema select",
)
PIPELINE_SOURCE_TABLE_SELECT = FragmentRegion(
    id="pipeline-source-table-select",
    selector="#pipeline-source-table-select",
    description="Pipeline source table select",
)
PIPELINE_TARGET_SCHEMA_SELECT = FragmentRegion(
    id="pipeline-target-schema-select",
    selector="#pipeline-target-schema-select",
    description="Pipeline destination schema select",
)
PIPELINE_TARGET_TABLE_SELECT = FragmentRegion(
    id="pipeline-target-table-select",
    selector="#pipeline-target-table-select",
    description="Pipeline destination table select",
)
PIPELINE_PREVIEW_REGION = FragmentRegion(
    id="pipeline-preview-region",
    selector="#pipeline-preview-region",
    description="Pipeline preview refresh region",
)
PIPELINE_RUN_MONITOR = FragmentRegion(
    id="pipeline-run-monitor",
    selector="#pipeline-run-monitor",
    description="Persisted pipeline run status and events",
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
USER_DIRECTORY_BODY = FragmentRegion(
    id="user-directory-body",
    selector="#user-directory-body",
    description="Administrator user directory table body",
)
AUDIT_RESULTS = FragmentRegion(
    id="audit-results-region",
    selector="#audit-results-region",
    description="Audit log results",
)
AUDIT_RESULTS_LAZY_BODY = FragmentRegion(
    id="audit-results-region-body",
    selector="#audit-results-region-body",
    description="Hedron Lazy inner slot for audit results",
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
SECURITY_ACTIVITY_LAZY_BODY = FragmentRegion(
    id="security-activity-body",
    selector="#security-activity-body",
    description="Hedron Lazy inner slot for security activity",
)
GLOBAL_FEEDBACK = FragmentRegion(
    id="global-feedback",
    selector="#global-feedback",
    description="Global request feedback",
)

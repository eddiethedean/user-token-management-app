"""Shared Annotated Path/Form/Query aliases for UI route handlers.

FastAPI 0.141+ requires defaults on the parameter (``param: Alias = default``),
not inside ``Query(...)`` / ``Form(...)`` within ``Annotated``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import File, Form, Path, Query, UploadFile

# UUID primary keys (``app.models.new_id`` → ``str(uuid.uuid4())``).
_UUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"

# --- Path ---

UserIdPath = Annotated[str, Path(min_length=36, max_length=36, pattern=_UUID_PATTERN)]
SessionIdPath = Annotated[str, Path(min_length=36, max_length=36, pattern=_UUID_PATTERN)]
InvitationIdPath = Annotated[str, Path(min_length=36, max_length=36, pattern=_UUID_PATTERN)]
SecretProviderPath = Annotated[Literal["mss", "mcscop", "postgres"], Path()]

# --- Query (defaults at call site) ---

NextQuery = Annotated[str, Query(max_length=2048)]
PasswordNoticeQuery = Annotated[str, Query(max_length=64)]
UpdatedQuery = Annotated[bool, Query()]
NoticeQuery = Annotated[str, Query(max_length=64)]
SearchQuery = Annotated[str, Query(max_length=160)]
StatusFilterQuery = Annotated[str, Query(max_length=20)]
PageQuery = Annotated[int, Query(ge=1)]
EventTypeQuery = Annotated[str, Query(max_length=100)]
OutcomeQuery = Annotated[str, Query(max_length=20)]
FlowTokenQuery = Annotated[str, Query(max_length=512)]

# --- Form (defaults at call site when optional) ---

EmailForm = Annotated[str, Form(max_length=320)]
LoginEmailForm = Annotated[str, Form(max_length=320)]
PasswordForm = Annotated[str, Form(max_length=128)]
OptionalPasswordForm = Annotated[str, Form(max_length=128)]
PasswordConfirmForm = Annotated[str, Form(max_length=128)]
OptionalPasswordConfirmForm = Annotated[str, Form(max_length=128)]
PreauthCsrfForm = Annotated[str, Form(max_length=256)]
NextForm = Annotated[str, Form(max_length=2048)]
FullNameForm = Annotated[str, Form(max_length=160)]
OrganizationForm = Annotated[str, Form(max_length=160)]
JobTitleForm = Annotated[str, Form(max_length=160)]
PhoneForm = Annotated[str, Form(max_length=40)]
FlowTokenForm = Annotated[str, Form(max_length=512)]
RoleForm = Annotated[str, Form(max_length=64)]
ListingQueryForm = Annotated[str, Form(max_length=160)]
ListingStatusForm = Annotated[str, Form(max_length=20)]
ListingPageForm = Annotated[int, Form(ge=1)]
PipelineIdForm = Annotated[str, Form(max_length=36)]
PipelineNameForm = Annotated[str, Form(min_length=1, max_length=120)]
PipelineProviderForm = Annotated[Literal["mss", "mcscop", "postgres"], Form()]
PipelineSourceProviderForm = Annotated[Literal["mss", "mcscop", "postgres", "csv"], Form()]
PipelineSchemaForm = Annotated[str, Form(min_length=1, max_length=80)]
PipelineTableForm = Annotated[str, Form(min_length=1, max_length=80)]
PipelineOptionalTableForm = Annotated[str, Form(max_length=80)]
PipelineWriteModeForm = Annotated[Literal["upsert", "append", "replace"], Form()]
ThemeNameForm = Annotated[str, Form(max_length=64)]
ColorModeForm = Annotated[Literal["system", "light", "dark"], Form()]
CsvUploadForm = Annotated[UploadFile, File()]

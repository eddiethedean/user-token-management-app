"""Shared Annotated Path/Form/Query aliases for UI route handlers.

FastAPI 0.141+ requires defaults on the parameter (``param: Alias = default``),
not inside ``Query(...)`` / ``Form(...)`` within ``Annotated``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import Form, Path, Query

# UUID primary keys (``app.models.new_id`` → ``str(uuid.uuid4())``).
_UUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"

# --- Path ---

UserIdPath = Annotated[str, Path(min_length=36, max_length=36, pattern=_UUID_PATTERN)]
SessionIdPath = Annotated[str, Path(min_length=36, max_length=36, pattern=_UUID_PATTERN)]
InvitationIdPath = Annotated[str, Path(min_length=36, max_length=36, pattern=_UUID_PATTERN)]
SecretProviderPath = Annotated[Literal["advana", "ade", "mss"], Path()]

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
SecretTokenForm = Annotated[str, Form(max_length=8192)]
RoleForm = Annotated[str, Form(max_length=64)]
ListingQueryForm = Annotated[str, Form(max_length=160)]
ListingStatusForm = Annotated[str, Form(max_length=20)]
ListingPageForm = Annotated[int, Form(ge=1)]

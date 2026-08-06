"""Shared Annotated Form/Query aliases for UI route handlers.

FastAPI 0.141+ requires defaults on the parameter (`param: Alias = default`),
not inside ``Query(...)`` / ``Form(...)`` within ``Annotated``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Form, Query

# --- Query (defaults at call site) ---

NextQuery = Annotated[str, Query()]
PasswordNoticeQuery = Annotated[str, Query()]
UpdatedQuery = Annotated[bool, Query()]
NoticeQuery = Annotated[str, Query()]
SearchQuery = Annotated[str, Query(max_length=160)]
StatusFilterQuery = Annotated[str, Query()]
PageQuery = Annotated[int, Query(ge=1)]
EventTypeQuery = Annotated[str, Query()]
OutcomeQuery = Annotated[str, Query()]
FlowTokenQuery = Annotated[str, Query()]

# --- Form (defaults at call site when optional) ---

EmailForm = Annotated[str, Form(max_length=320)]
LoginEmailForm = Annotated[str, Form()]
PasswordForm = Annotated[str, Form(max_length=128)]
OptionalPasswordForm = Annotated[str, Form(max_length=128)]
PasswordConfirmForm = Annotated[str, Form(max_length=128)]
OptionalPasswordConfirmForm = Annotated[str, Form(max_length=128)]
PreauthCsrfForm = Annotated[str, Form(max_length=256)]
NextForm = Annotated[str, Form(max_length=2048)]
FullNameForm = Annotated[str, Form(max_length=160)]
OrganizationForm = Annotated[str, Form()]
JobTitleForm = Annotated[str, Form()]
PhoneForm = Annotated[str, Form()]
FlowTokenForm = Annotated[str, Form(max_length=512)]
SecretTokenForm = Annotated[str, Form(max_length=8192)]
RoleForm = Annotated[str, Form(max_length=64)]
ListingQueryForm = Annotated[str, Form()]
ListingStatusForm = Annotated[str, Form()]
ListingPageForm = Annotated[int, Form()]

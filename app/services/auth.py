"""Auth service façade — stable import path for sessions, registration, invitations, and resets."""

from __future__ import annotations

from app.services.auth_common import (
    AccountLockedError,
    AuthenticationError,
    RegistrationPendingError,
    SessionTokens,
    TokenFlowError,
    _lock_role,
    ensure_default_roles,
    lock_administrator_action,
)
from app.services.invitations import (
    accept_invitation,
    create_invitation,
    get_valid_invitation,
    revoke_invitation,
)
from app.services.password_reset import (
    complete_password_reset,
    get_valid_password_reset,
    request_password_reset,
)
from app.services.registration import (
    approve_self_registration,
    complete_self_registration,
    deny_self_registration,
    get_valid_registration_verification,
    request_self_registration,
)
from app.services.sessions import (
    authenticate_trusted_identity,
    authenticate_user,
    create_session,
    revoke_all_sessions,
    revoke_session,
    rotate_session,
)

__all__ = [
    "AccountLockedError",
    "AuthenticationError",
    "RegistrationPendingError",
    "SessionTokens",
    "TokenFlowError",
    "_lock_role",
    "accept_invitation",
    "approve_self_registration",
    "authenticate_trusted_identity",
    "authenticate_user",
    "complete_password_reset",
    "complete_self_registration",
    "create_invitation",
    "create_session",
    "deny_self_registration",
    "ensure_default_roles",
    "get_valid_invitation",
    "get_valid_password_reset",
    "get_valid_registration_verification",
    "lock_administrator_action",
    "request_password_reset",
    "request_self_registration",
    "revoke_all_sessions",
    "revoke_invitation",
    "revoke_session",
    "rotate_session",
]

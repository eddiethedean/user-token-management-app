"""SQLAlchemy adapter for the application credential-resolution port."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.orm import Session

from app.application.ports import CredentialResolver, RequestMetadata
from app.config import Settings
from app.models import User


@dataclass(frozen=True)
class SqlAlchemyCredentialResolver(CredentialResolver):
    """Resolve encrypted credentials only for an owner and explicit purpose."""

    db: Session
    settings: Settings
    user: User
    request: Request | RequestMetadata | None = None

    def __call__(self, *, user_id: str, provider: str, purpose: str) -> dict[str, str]:
        if user_id != self.user.id:
            raise PermissionError("The requested connection is not owned by this user.")
        from app.services.secrets import decrypt_user_credentials_for_run

        return decrypt_user_credentials_for_run(
            self.db,
            self.settings,
            user=self.user,
            provider=provider,
            request=self.request,
            purpose=purpose,
        )

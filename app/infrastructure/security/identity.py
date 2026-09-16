"""SQLAlchemy adapter for the framework-neutral login use case."""

from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from app.application.dto import ActorContext
from app.application.identity import LoginInput, LoginResult
from app.config import Settings
from app.services.auth import authenticate_user


class SqlAlchemyPasswordGateway:
    def __init__(self, db: Session, settings: Settings, request: Request) -> None:
        self.db = db
        self.settings = settings
        self.request = request

    def authenticate_password(self, input: LoginInput, actor: ActorContext) -> LoginResult:
        user = authenticate_user(
            self.db,
            self.settings,
            input.email,
            input.password,
            self.request,
        )
        return LoginResult(
            user_id=user.id,
            preferred_color_mode=user.preferred_color_mode,
        )

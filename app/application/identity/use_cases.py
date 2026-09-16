"""Authentication orchestration without HTTP, ORM, or token implementations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.application.dto import ActorContext


@dataclass(frozen=True)
class LoginInput:
    email: str
    password: str


@dataclass(frozen=True)
class LoginResult:
    user_id: str
    preferred_color_mode: str = "dark"
    access_token: str = ""
    refresh_token: str = ""


class IdentityGateway(Protocol):
    def authenticate_password(self, input: LoginInput, actor: ActorContext) -> LoginResult: ...


class LoginUseCase:
    """Delegate provider-specific authentication to an injected gateway."""

    def __init__(self, gateway: IdentityGateway) -> None:
        self.gateway = gateway

    def execute(self, *, input: LoginInput, actor: ActorContext) -> LoginResult:
        if not input.email.strip() or not input.password:
            raise ValueError("Unable to sign in with those credentials.")
        return self.gateway.authenticate_password(input, actor)

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class TokenRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int


class RegistrationRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(default="", max_length=160)


class ProfileUpdate(BaseModel):
    full_name: str = Field(default="", max_length=160)
    organization: str = Field(default="", max_length=160)
    job_title: str = Field(default="", max_length=160)
    phone: str = Field(default="", max_length=40)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=128)
    new_password: str = Field(max_length=128)


class UserView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    full_name: str
    organization: str
    job_title: str
    phone: str
    status: str
    created_at: datetime
    last_login_at: datetime | None
    roles: list[str]

    @classmethod
    def from_user(cls, user) -> "UserView":
        return cls(
            id=user.id,
            email=user.email_original,
            full_name=user.full_name,
            organization=user.organization,
            job_title=user.job_title,
            phone=user.phone,
            status=user.status,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
            roles=user.role_names,
        )


class SessionView(BaseModel):
    id: str
    created_at: datetime
    last_seen_at: datetime
    absolute_expires_at: datetime
    user_agent: str
    source_ip: str
    current: bool


class SecretTokenRequest(BaseModel):
    token: str = Field(min_length=8, max_length=8192)


class SecretSlotView(BaseModel):
    provider: str
    label: str
    environment_variable: str
    configured: bool
    updated_at: datetime | None = None
    last_used_at: datetime | None = None


class InvitationRequest(BaseModel):
    email: EmailStr
    role: str = Field(default="user", max_length=64)


class AdminUserUpdate(BaseModel):
    status: str | None = None
    roles: list[str] | None = None

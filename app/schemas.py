from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class TokenRequest(BaseModel):
    email: EmailStr
    password: str


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
    current_password: str
    new_password: str


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


class InvitationRequest(BaseModel):
    email: EmailStr
    role: str = "user"


class AdminUserUpdate(BaseModel):
    status: str | None = None
    roles: list[str] | None = None

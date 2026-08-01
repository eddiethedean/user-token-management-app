from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"
    app_name: str = "Access Registry"
    public_base_url: str = "http://127.0.0.1:8000"
    database_url: str = f"sqlite:///{BASE_DIR / 'access-registry.db'}"

    jwt_secret: str = "development-only-jwt-secret-change-me"
    session_pepper: str = "development-only-session-pepper-change-me"
    csrf_secret: str = "development-only-csrf-secret-change-me"
    jwt_issuer: str = "urn:access-registry:local"
    jwt_audience: str = "access-registry-api"
    access_token_minutes: int = Field(default=10, ge=1, le=60)
    refresh_token_hours: int = Field(default=8, ge=1, le=168)
    session_idle_minutes: int = Field(default=30, ge=5, le=1440)
    cookie_secure: bool = False
    cookie_path: str = "/"

    allowed_email_domains: str = ""
    email_backend: Literal["console", "smtp"] = "console"
    email_from: str = "Access Registry <no-reply@example.gov>"
    smtp_host: str = ""
    smtp_port: int = 25
    smtp_starttls: bool = True
    smtp_username: str = ""
    smtp_password: str = ""

    password_hash_scheme: Literal["argon2", "pbkdf2_sha256"] = "argon2"
    pbkdf2_iterations: int = Field(default=600_000, ge=100_000)

    @property
    def email_domain_allowlist(self) -> set[str]:
        return {
            domain.strip().lower().lstrip("@")
            for domain in self.allowed_email_domains.split(",")
            if domain.strip()
        }

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        if self.is_production:
            weak_markers = ("development-only", "replace-with")
            for name in ("jwt_secret", "session_pepper", "csrf_secret"):
                value = getattr(self, name)
                if len(value) < 32 or any(marker in value for marker in weak_markers):
                    raise ValueError(f"{name.upper()} must be a strong production secret")
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true in production")
            if not self.email_domain_allowlist:
                raise ValueError("ALLOWED_EMAIL_DOMAINS must be configured in production")
            if self.email_backend == "smtp" and not self.smtp_host:
                raise ValueError("SMTP_HOST is required when EMAIL_BACKEND=smtp")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


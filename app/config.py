from __future__ import annotations

import base64
import re
from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from email_validator import EmailNotValidError, validate_email
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"
    app_name: str = Field(default="Access Registry", min_length=1, max_length=100)
    public_base_url: str = "http://127.0.0.1:8000"
    database_url: str = f"sqlite:///{BASE_DIR / 'access-registry.db'}"

    jwt_secret: str = "development-only-jwt-secret-change-me"
    session_pepper: str = "development-only-session-pepper-change-me"
    csrf_secret: str = "development-only-csrf-secret-change-me"
    api_token_encryption_keys: dict[str, str] = Field(
        default_factory=lambda: {"development-v1": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}
    )
    api_token_active_key_id: str = "development-v1"
    api_token_max_wraps_per_key: int = Field(default=1_000_000, ge=1, le=100_000_000)
    jwt_issuer: str = "urn:access-registry:local"
    jwt_audience: str = "access-registry-api"
    authentication_mode: Literal["local_password", "trusted_header"] = "local_password"
    password_only_production_risk_accepted: bool = False
    trusted_identity_header: str = "x-access-registry-user"
    access_token_minutes: int = Field(default=10, ge=1, le=60)
    refresh_token_hours: int = Field(default=8, ge=1, le=168)
    session_idle_minutes: int = Field(default=30, ge=5, le=1440)
    cookie_secure: bool = False
    cookie_path: str = "auto"
    hsts_include_subdomains: bool = False
    trusted_proxy_ips: str = ""

    db_pool_size: int = Field(default=5, ge=1, le=100)
    db_max_overflow: int = Field(default=10, ge=0, le=100)
    db_pool_timeout: int = Field(default=30, ge=1, le=300)
    db_pool_recycle: int = Field(default=1800, ge=0, le=86400)

    rate_limit_enabled: bool = True
    rate_limit_window_seconds: int = Field(default=60, ge=10, le=3600)
    rate_limit_login_per_source: int = Field(default=30, ge=1, le=1000)
    rate_limit_login_per_account: int = Field(default=10, ge=1, le=1000)
    rate_limit_registration_per_source: int = Field(default=10, ge=1, le=1000)
    rate_limit_registration_per_account: int = Field(default=3, ge=1, le=1000)
    rate_limit_reset_per_source: int = Field(default=10, ge=1, le=1000)
    rate_limit_reset_per_account: int = Field(default=3, ge=1, le=1000)

    directory_lookup_url: str = ""
    directory_lookup_timeout_seconds: float = Field(default=5.0, ge=0.5, le=30)
    directory_lookup_verify_tls: bool = True
    directory_lookup_ca_bundle: str = ""
    directory_lookup_required: bool = False
    directory_lookup_bearer_token: str = ""

    allowed_email_domains: str = ""
    email_backend: Literal["console", "smtp"] = "console"
    email_redact_sent_bodies: bool = False
    email_max_attempts: int = Field(default=5, ge=1, le=20)
    email_retry_base_seconds: int = Field(default=30, ge=1, le=3600)
    email_retry_max_seconds: int = Field(default=3600, ge=1, le=86400)
    email_claim_timeout_seconds: int = Field(default=300, ge=30, le=3600)
    email_from: str = Field(
        default="Access Registry <no-reply@example.gov>", min_length=3, max_length=320
    )
    smtp_host: str = ""
    smtp_port: int = Field(default=25, ge=1, le=65535)
    smtp_starttls: bool = True
    smtp_ca_bundle: str = ""
    smtp_username: str = ""
    smtp_password: str = ""

    password_hash_scheme: Literal["argon2", "pbkdf2_sha256"] = "argon2"
    pbkdf2_iterations: int = Field(default=600_000, ge=100_000)
    password_blocklist_path: str = ""

    @property
    def email_domain_allowlist(self) -> set[str]:
        return {
            domain.strip().lower().lstrip("@")
            for domain in self.allowed_email_domains.split(",")
            if domain.strip()
        }

    @property
    def trusted_proxy_ip_set(self) -> set[str]:
        return {
            str(ip_address(value.strip()))
            for value in self.trusted_proxy_ips.split(",")
            if value.strip()
        }

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def api_token_key_ring(self) -> dict[str, bytes]:
        keys: dict[str, bytes] = {}
        for key_id, encoded in self.api_token_encryption_keys.items():
            try:
                decoded = base64.b64decode(encoded, altchars=b"-_", validate=True)
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    f"API_TOKEN_ENCRYPTION_KEYS entry {key_id!r} must be valid base64"
                ) from exc
            if len(decoded) != 32:
                raise ValueError(
                    f"API_TOKEN_ENCRYPTION_KEYS entry {key_id!r} must decode to 32 bytes"
                )
            keys[key_id] = decoded
        return keys

    @model_validator(mode="after")
    def validate_production_settings(self) -> Settings:
        for name in (
            "app_name",
            "directory_lookup_bearer_token",
            "email_from",
            "jwt_audience",
            "jwt_issuer",
            "smtp_host",
            "smtp_username",
        ):
            value = getattr(self, name)
            if any(ord(character) < 32 or ord(character) == 127 for character in value):
                raise ValueError(f"{name.upper()} cannot contain control characters")
        if self.smtp_host and any(character.isspace() for character in self.smtp_host):
            raise ValueError("SMTP_HOST cannot contain whitespace")
        if self.smtp_password and not self.smtp_username:
            raise ValueError("SMTP_PASSWORD requires SMTP_USERNAME")
        try:
            validate_email(self.email_from, check_deliverability=False, allow_display_name=True)
        except EmailNotValidError as exc:
            raise ValueError("EMAIL_FROM must contain a valid email address") from exc
        if not self.api_token_encryption_keys:
            raise ValueError("API_TOKEN_ENCRYPTION_KEYS must contain at least one key")
        if any(
            not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", key_id)
            for key_id in self.api_token_encryption_keys
        ):
            raise ValueError("API token encryption key IDs contain unsupported characters")
        key_ring = self.api_token_key_ring
        if self.api_token_active_key_id not in key_ring:
            raise ValueError("API_TOKEN_ACTIVE_KEY_ID must identify a configured encryption key")
        if self.cookie_path != "auto" and (
            not self.cookie_path.startswith("/")
            or self.cookie_path.startswith("//")
            or "\\" in self.cookie_path
            or "?" in self.cookie_path
            or "#" in self.cookie_path
            or any(ord(character) < 32 or ord(character) == 127 for character in self.cookie_path)
        ):
            raise ValueError("COOKIE_PATH must be 'auto' or a safe absolute path")
        try:
            _ = self.trusted_proxy_ip_set
        except ValueError as exc:
            raise ValueError("TRUSTED_PROXY_IPS must contain only IP addresses") from exc
        if self.directory_lookup_url:
            parsed_directory_url = urlsplit(self.directory_lookup_url)
            try:
                _ = parsed_directory_url.port
            except ValueError as exc:
                raise ValueError("DIRECTORY_LOOKUP_URL contains an invalid port") from exc
            if (
                parsed_directory_url.scheme not in {"http", "https"}
                or not parsed_directory_url.hostname
                or parsed_directory_url.username
                or parsed_directory_url.password
                or parsed_directory_url.fragment
                or "\\" in self.directory_lookup_url
                or any(character.isspace() for character in self.directory_lookup_url)
            ):
                raise ValueError(
                    "DIRECTORY_LOOKUP_URL must be an absolute HTTP(S) URL without credentials or fragment"
                )
        if self.directory_lookup_ca_bundle and not Path(self.directory_lookup_ca_bundle).is_file():
            raise ValueError("DIRECTORY_LOOKUP_CA_BUNDLE must identify a readable file")
        if self.smtp_ca_bundle and not Path(self.smtp_ca_bundle).is_file():
            raise ValueError("SMTP_CA_BUNDLE must identify a readable file")
        if self.email_retry_max_seconds < self.email_retry_base_seconds:
            raise ValueError("EMAIL_RETRY_MAX_SECONDS must be at least EMAIL_RETRY_BASE_SECONDS")
        if not re.fullmatch(r"[a-z0-9-]{1,64}", self.trusted_identity_header):
            raise ValueError("TRUSTED_IDENTITY_HEADER must be a lowercase HTTP header name")
        if self.trusted_identity_header in {
            "authorization",
            "cookie",
            "forwarded",
            "host",
            "rstudio-connect-app-base-url",
            "x-forwarded-for",
            "x-forwarded-host",
            "x-forwarded-port",
            "x-forwarded-proto",
        }:
            raise ValueError("TRUSTED_IDENTITY_HEADER cannot use a reserved security header")
        if self.is_production:
            weak_markers = ("development-only", "replace-with")
            for name in ("jwt_secret", "session_pepper", "csrf_secret"):
                value = getattr(self, name)
                if len(value) < 32 or any(marker in value for marker in weak_markers):
                    raise ValueError(f"{name.upper()} must be a strong production secret")
            if self.api_token_active_key_id.startswith("development-") or any(
                not any(key) for key in key_ring.values()
            ):
                raise ValueError("API token encryption keys must be replaced in production")
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true in production")
            if not self.email_domain_allowlist:
                raise ValueError("ALLOWED_EMAIL_DOMAINS must be configured in production")
            parsed_public_url = urlsplit(self.public_base_url)
            try:
                _ = parsed_public_url.port
            except ValueError as exc:
                raise ValueError("PUBLIC_BASE_URL contains an invalid port") from exc
            if (
                parsed_public_url.scheme != "https"
                or not parsed_public_url.hostname
                or parsed_public_url.username
                or parsed_public_url.password
                or parsed_public_url.query
                or parsed_public_url.fragment
                or parsed_public_url.path.startswith("//")
                or "\\" in self.public_base_url
                or any(character.isspace() for character in self.public_base_url)
            ):
                raise ValueError(
                    "PUBLIC_BASE_URL must be an absolute HTTPS URL without credentials, query, or fragment"
                )
            try:
                database_url = make_url(self.database_url)
            except (ArgumentError, TypeError, ValueError) as exc:
                raise ValueError("DATABASE_URL must be a valid SQLAlchemy URL") from exc
            if database_url.drivername != "postgresql+psycopg" or not database_url.database:
                raise ValueError(
                    "DATABASE_URL must use the PostgreSQL psycopg driver and identify a database"
                )
            if self.email_backend != "smtp" or not self.smtp_host:
                raise ValueError("Production email requires EMAIL_BACKEND=smtp and SMTP_HOST")
            if not self.smtp_starttls:
                raise ValueError("SMTP_STARTTLS must be true in production")
            if self.authentication_mode == "local_password":
                if not self.password_only_production_risk_accepted:
                    raise ValueError(
                        "Local-password production requires explicit password-only risk acceptance"
                    )
            elif not self.trusted_proxy_ip_set:
                raise ValueError("Trusted-header authentication requires TRUSTED_PROXY_IPS")
            if self.directory_lookup_url and urlsplit(self.directory_lookup_url).scheme != "https":
                raise ValueError("DIRECTORY_LOOKUP_URL must use HTTPS in production")
            if self.directory_lookup_url and not self.directory_lookup_verify_tls:
                raise ValueError("DIRECTORY_LOOKUP_VERIFY_TLS must be true in production")
            if not self.email_redact_sent_bodies:
                raise ValueError("EMAIL_REDACT_SENT_BODIES must be true in production")
            if not self.password_blocklist_path:
                raise ValueError("PASSWORD_BLOCKLIST_PATH is required in production")
            blocklist_path = Path(self.password_blocklist_path)
            if not blocklist_path.is_file():
                raise ValueError("PASSWORD_BLOCKLIST_PATH must identify a readable file")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

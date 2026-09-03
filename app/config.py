from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from email_validator import EmailNotValidError, validate_email
from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class ConfigDefaults:
    """Committed, non-sensitive product defaults.

    Deployment-specific values and credentials intentionally remain environment
    settings. An operator can override any of these defaults with an
    environment variable when a deployment has a documented requirement.
    """

    app_env: Literal["development", "test", "production"] = "development"
    app_name: str = "Data Mover"
    custom_theme_enabled: bool = True
    public_base_url: str = "http://127.0.0.1:8000"
    database_url: str = f"sqlite:///{BASE_DIR / 'access-registry.db'}"
    api_token_max_wraps_per_key: int = 1_000_000
    jwt_issuer: str = "urn:access-registry:local"
    jwt_audience: str = "access-registry-api"
    authentication_mode: Literal["local_password", "trusted_header"] = "local_password"
    password_only_production_risk_accepted: bool = False
    trusted_identity_header: str = "x-access-registry-user"
    access_token_minutes: int = 10
    refresh_token_hours: int = 8
    session_idle_minutes: int = 30
    cookie_secure: bool = False
    cookie_path: str = "auto"
    hsts_include_subdomains: bool = False
    trusted_proxy_ips: str = ""
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: int = 30
    db_pool_recycle: int = 1_800
    rate_limit_enabled: bool = True
    rate_limit_window_seconds: int = 60
    rate_limit_login_per_source: int = 30
    rate_limit_login_per_account: int = 10
    rate_limit_registration_per_source: int = 10
    rate_limit_registration_per_account: int = 3
    rate_limit_reset_per_source: int = 10
    rate_limit_reset_per_account: int = 3
    directory_lookup_timeout_seconds: float = 5.0
    directory_lookup_verify_tls: bool = True
    directory_lookup_required: bool = False
    email_backend: Literal["console", "smtp"] = "console"
    email_redact_sent_bodies: bool = False
    email_max_attempts: int = 5
    email_retry_base_seconds: int = 30
    email_retry_max_seconds: int = 3_600
    email_claim_timeout_seconds: int = 300
    email_from: str = "Data Mover <no-reply@example.gov>"
    smtp_port: int = 25
    smtp_starttls: bool = True
    smtp_allow_legacy_port25_fallback: bool = False
    password_hash_scheme: Literal["argon2", "pbkdf2_sha256"] = "argon2"
    pbkdf2_iterations: int = 600_000
    data_mover_mode: Literal["demo", "real"] = "demo"
    pipeline_worker_id: str = ""
    pipeline_background_poll_seconds: float = 2.0
    pipeline_janitor_interval_seconds: int = 3_600
    pipeline_lease_seconds: int = 120
    pipeline_batch_rows: int = 25_000
    pipeline_batch_target_bytes: int = 67_108_864
    pipeline_max_run_seconds: int = 14_400
    pipeline_max_source_bytes: int = 1_073_741_824
    pipeline_max_spool_bytes: int = 2_147_483_648
    pipeline_http_connect_seconds: float = 10
    pipeline_http_read_seconds: float = 120
    pipeline_http_write_seconds: float = 120
    pipeline_http_retry_attempts: int = 3
    pipeline_catalog_ttl_seconds: int = 300
    pipeline_connection_max_age_seconds: int = 900
    pipeline_run_retention_days: int = 90
    pipeline_event_retention_days: int = 30
    pipeline_enable_postgres_writer: bool = True
    pipeline_enable_mss_writer: bool = False
    pipeline_enable_mcscop_writer: bool = False
    pipeline_apply_internal_ca_fix: bool = False


CONFIG_DEFAULTS = ConfigDefaults()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = CONFIG_DEFAULTS.app_env
    app_name: str = Field(default=CONFIG_DEFAULTS.app_name, min_length=1, max_length=100)
    custom_theme_enabled: bool = CONFIG_DEFAULTS.custom_theme_enabled
    public_base_url: str = CONFIG_DEFAULTS.public_base_url
    database_url: str = CONFIG_DEFAULTS.database_url

    jwt_secret: str = "development-only-jwt-secret-change-me"
    session_pepper: str = "development-only-session-pepper-change-me"
    csrf_secret: str = "development-only-csrf-secret-change-me"
    api_token_encryption_keys: dict[str, str] = Field(
        default_factory=lambda: {"development-v1": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}
    )
    api_token_active_key_id: str = "development-v1"
    api_token_max_wraps_per_key: int = Field(
        default=CONFIG_DEFAULTS.api_token_max_wraps_per_key, ge=1, le=100_000_000
    )
    jwt_issuer: str = CONFIG_DEFAULTS.jwt_issuer
    jwt_audience: str = CONFIG_DEFAULTS.jwt_audience
    authentication_mode: Literal["local_password", "trusted_header"] = (
        CONFIG_DEFAULTS.authentication_mode
    )
    password_only_production_risk_accepted: bool = (
        CONFIG_DEFAULTS.password_only_production_risk_accepted
    )
    trusted_identity_header: str = CONFIG_DEFAULTS.trusted_identity_header
    access_token_minutes: int = Field(default=CONFIG_DEFAULTS.access_token_minutes, ge=1, le=60)
    refresh_token_hours: int = Field(default=CONFIG_DEFAULTS.refresh_token_hours, ge=1, le=168)
    session_idle_minutes: int = Field(default=CONFIG_DEFAULTS.session_idle_minutes, ge=5, le=1440)
    cookie_secure: bool = CONFIG_DEFAULTS.cookie_secure
    cookie_path: str = CONFIG_DEFAULTS.cookie_path
    hsts_include_subdomains: bool = CONFIG_DEFAULTS.hsts_include_subdomains
    trusted_proxy_ips: str = CONFIG_DEFAULTS.trusted_proxy_ips

    db_pool_size: int = Field(default=CONFIG_DEFAULTS.db_pool_size, ge=1, le=100)
    db_max_overflow: int = Field(default=CONFIG_DEFAULTS.db_max_overflow, ge=0, le=100)
    db_pool_timeout: int = Field(default=CONFIG_DEFAULTS.db_pool_timeout, ge=1, le=300)
    db_pool_recycle: int = Field(default=CONFIG_DEFAULTS.db_pool_recycle, ge=0, le=86400)

    rate_limit_enabled: bool = CONFIG_DEFAULTS.rate_limit_enabled
    rate_limit_window_seconds: int = Field(
        default=CONFIG_DEFAULTS.rate_limit_window_seconds, ge=10, le=3600
    )
    rate_limit_login_per_source: int = Field(
        default=CONFIG_DEFAULTS.rate_limit_login_per_source, ge=1, le=1000
    )
    rate_limit_login_per_account: int = Field(
        default=CONFIG_DEFAULTS.rate_limit_login_per_account, ge=1, le=1000
    )
    rate_limit_registration_per_source: int = Field(
        default=CONFIG_DEFAULTS.rate_limit_registration_per_source, ge=1, le=1000
    )
    rate_limit_registration_per_account: int = Field(
        default=CONFIG_DEFAULTS.rate_limit_registration_per_account, ge=1, le=1000
    )
    rate_limit_reset_per_source: int = Field(
        default=CONFIG_DEFAULTS.rate_limit_reset_per_source, ge=1, le=1000
    )
    rate_limit_reset_per_account: int = Field(
        default=CONFIG_DEFAULTS.rate_limit_reset_per_account, ge=1, le=1000
    )

    directory_lookup_url: str = ""
    directory_lookup_timeout_seconds: float = Field(
        default=CONFIG_DEFAULTS.directory_lookup_timeout_seconds,
        ge=0.5,
        le=30,
        validation_alias=AliasChoices(
            "DIRECTORY_LOOKUP_TIMEOUT_SECONDS", "DIRECTORY_LOOKUP_TIMEOUT_S"
        ),
    )
    directory_lookup_verify_tls: bool = Field(
        default=CONFIG_DEFAULTS.directory_lookup_verify_tls,
        validation_alias=AliasChoices("DIRECTORY_LOOKUP_VERIFY_TLS", "DIRECTORY_LOOKUP_VERIFY_SSL"),
    )
    directory_lookup_ca_bundle: str = ""
    directory_lookup_required: bool = CONFIG_DEFAULTS.directory_lookup_required
    directory_lookup_bearer_token: str = ""

    # Deployment policy: keep the enrollment allowlist in .env, not in the
    # committed defaults, because it varies by environment.
    allowed_email_domains: str = ""
    email_backend: Literal["console", "smtp"] = CONFIG_DEFAULTS.email_backend
    email_redact_sent_bodies: bool = CONFIG_DEFAULTS.email_redact_sent_bodies
    email_max_attempts: int = Field(default=CONFIG_DEFAULTS.email_max_attempts, ge=1, le=20)
    email_retry_base_seconds: int = Field(
        default=CONFIG_DEFAULTS.email_retry_base_seconds, ge=1, le=3600
    )
    email_retry_max_seconds: int = Field(
        default=CONFIG_DEFAULTS.email_retry_max_seconds, ge=1, le=86400
    )
    email_claim_timeout_seconds: int = Field(
        default=CONFIG_DEFAULTS.email_claim_timeout_seconds, ge=30, le=3600
    )
    email_from: str = Field(
        default=CONFIG_DEFAULTS.email_from,
        min_length=3,
        max_length=320,
        validation_alias=AliasChoices("EMAIL_FROM", "SMTP_FROM_EMAIL"),
    )
    smtp_host: str = ""
    smtp_port: int = Field(default=CONFIG_DEFAULTS.smtp_port, ge=1, le=65535)
    smtp_starttls: bool = Field(
        default=CONFIG_DEFAULTS.smtp_starttls,
        validation_alias=AliasChoices("SMTP_STARTTLS", "SMTP_USE_TLS"),
    )
    smtp_allow_legacy_port25_fallback: bool = CONFIG_DEFAULTS.smtp_allow_legacy_port25_fallback
    smtp_ca_bundle: str = ""
    smtp_username: str = ""
    smtp_password: str = ""

    password_hash_scheme: Literal["argon2", "pbkdf2_sha256"] = CONFIG_DEFAULTS.password_hash_scheme
    pbkdf2_iterations: int = Field(default=CONFIG_DEFAULTS.pbkdf2_iterations, ge=100_000)
    password_blocklist_path: str = ""

    data_mover_mode: Literal["demo", "real"] = CONFIG_DEFAULTS.data_mover_mode
    pipeline_worker_id: str = CONFIG_DEFAULTS.pipeline_worker_id
    pipeline_background_poll_seconds: float = Field(
        default=CONFIG_DEFAULTS.pipeline_background_poll_seconds, ge=0.5, le=60
    )
    pipeline_janitor_interval_seconds: int = Field(
        default=CONFIG_DEFAULTS.pipeline_janitor_interval_seconds, ge=60, le=86_400
    )
    pipeline_lease_seconds: int = Field(
        default=CONFIG_DEFAULTS.pipeline_lease_seconds, ge=30, le=3600
    )
    pipeline_batch_rows: int = Field(
        default=CONFIG_DEFAULTS.pipeline_batch_rows, ge=1_000, le=250_000
    )
    pipeline_batch_target_bytes: int = Field(
        default=CONFIG_DEFAULTS.pipeline_batch_target_bytes, ge=1_048_576, le=536_870_912
    )
    pipeline_max_run_seconds: int = Field(
        default=CONFIG_DEFAULTS.pipeline_max_run_seconds, ge=60, le=86_400
    )
    pipeline_max_source_bytes: int = Field(
        default=CONFIG_DEFAULTS.pipeline_max_source_bytes, ge=1, le=1_099_511_627_776
    )
    pipeline_max_spool_bytes: int = Field(
        default=CONFIG_DEFAULTS.pipeline_max_spool_bytes, ge=1, le=2_199_023_255_552
    )
    pipeline_spool_root: str = ""
    pipeline_http_connect_seconds: float = Field(
        default=CONFIG_DEFAULTS.pipeline_http_connect_seconds, ge=1, le=60
    )
    pipeline_http_read_seconds: float = Field(
        default=CONFIG_DEFAULTS.pipeline_http_read_seconds, ge=5, le=600
    )
    pipeline_http_write_seconds: float = Field(
        default=CONFIG_DEFAULTS.pipeline_http_write_seconds, ge=5, le=600
    )
    pipeline_http_retry_attempts: int = Field(
        default=CONFIG_DEFAULTS.pipeline_http_retry_attempts, ge=0, le=8
    )
    pipeline_catalog_ttl_seconds: int = Field(
        default=CONFIG_DEFAULTS.pipeline_catalog_ttl_seconds, ge=30, le=3600
    )
    pipeline_connection_max_age_seconds: int = Field(
        default=CONFIG_DEFAULTS.pipeline_connection_max_age_seconds, ge=60, le=86_400
    )
    pipeline_run_retention_days: int = Field(
        default=CONFIG_DEFAULTS.pipeline_run_retention_days, ge=1, le=730
    )
    pipeline_event_retention_days: int = Field(
        default=CONFIG_DEFAULTS.pipeline_event_retention_days, ge=1, le=365
    )
    pipeline_allowed_https_hosts: str = ""
    pipeline_ca_bundle: str = ""
    pipeline_enable_postgres_writer: bool = CONFIG_DEFAULTS.pipeline_enable_postgres_writer
    pipeline_enable_mss_writer: bool = CONFIG_DEFAULTS.pipeline_enable_mss_writer
    pipeline_enable_mcscop_writer: bool = CONFIG_DEFAULTS.pipeline_enable_mcscop_writer
    pipeline_apply_internal_ca_fix: bool = CONFIG_DEFAULTS.pipeline_apply_internal_ca_fix

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
    def is_demo_mode(self) -> bool:
        return self.data_mover_mode == "demo"

    @property
    def allowed_https_hosts(self) -> set[str]:
        return {
            host.strip().casefold().rstrip(".")
            for host in self.pipeline_allowed_https_hosts.split(",")
            if host.strip()
        }

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
        if self.directory_lookup_required and not self.directory_lookup_url:
            raise ValueError(
                "DIRECTORY_LOOKUP_URL is required when DIRECTORY_LOOKUP_REQUIRED is true"
            )
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
        if self.data_mover_mode == "real" and not self.is_production:
            weak_markers = ("development-only", "replace-with")
            for name in ("jwt_secret", "session_pepper", "csrf_secret"):
                value = getattr(self, name)
                if len(value) < 32 or any(marker in value.casefold() for marker in weak_markers):
                    raise ValueError(f"{name.upper()} must be a strong real-mode secret")
            if self.api_token_active_key_id.startswith("development-") or any(
                not any(key) for key in key_ring.values()
            ):
                raise ValueError("API token encryption keys must be replaced in real mode")
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true in real mode")
        if self.is_production:
            weak_markers = ("development-only", "replace-with")
            for name in ("jwt_secret", "session_pepper", "csrf_secret"):
                value = getattr(self, name)
                if len(value) < 32 or any(marker in value.casefold() for marker in weak_markers):
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
            if self.smtp_allow_legacy_port25_fallback:
                raise ValueError("SMTP_ALLOW_LEGACY_PORT25_FALLBACK is not allowed in production")
            if self.authentication_mode == "local_password":
                if not self.password_only_production_risk_accepted:
                    raise ValueError(
                        "Local-password production requires explicit password-only risk acceptance"
                    )
            elif not self.trusted_proxy_ip_set:
                raise ValueError("Trusted-header authentication requires TRUSTED_PROXY_IPS")
            if self.directory_lookup_required and not self.directory_lookup_url:
                raise ValueError(
                    "DIRECTORY_LOOKUP_URL is required when DIRECTORY_LOOKUP_REQUIRED is true"
                )
            if self.directory_lookup_url and urlsplit(self.directory_lookup_url).scheme != "https":
                raise ValueError("DIRECTORY_LOOKUP_URL must use HTTPS in production")
            if self.directory_lookup_url and not self.directory_lookup_verify_tls:
                raise ValueError("DIRECTORY_LOOKUP_VERIFY_TLS must be true in production")
            if not self.rate_limit_enabled:
                raise ValueError("RATE_LIMIT_ENABLED must be true in production")
            if not self.email_redact_sent_bodies:
                raise ValueError("EMAIL_REDACT_SENT_BODIES must be true in production")
            if not self.password_blocklist_path:
                raise ValueError("PASSWORD_BLOCKLIST_PATH is required in production")
            blocklist_path = Path(self.password_blocklist_path)
            if not blocklist_path.is_file():
                raise ValueError("PASSWORD_BLOCKLIST_PATH must identify a readable file")
            if self.data_mover_mode != "real":
                raise ValueError("DATA_MOVER_MODE must be real in production")
        if self.data_mover_mode == "real":
            if not self.is_production:
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
            if database_url.drivername == "postgresql+psycopg":
                if not database_url.database:
                    raise ValueError("DATABASE_URL must identify a PostgreSQL database")
            elif database_url.drivername in {"sqlite", "sqlite+pysqlite"}:
                if not database_url.database or database_url.database == ":memory:":
                    raise ValueError("SQLite real mode requires a file-backed application database")
            else:
                raise ValueError(
                    "Real transfers require a SQLite or PostgreSQL application database"
                )
            if not self.pipeline_spool_root:
                raise ValueError("PIPELINE_SPOOL_ROOT is required in real mode")
            spool_root = Path(self.pipeline_spool_root)
            if not spool_root.exists() or not spool_root.is_dir():
                raise ValueError("PIPELINE_SPOOL_ROOT must identify a writable directory")
            if not self.allowed_https_hosts:
                raise ValueError("PIPELINE_ALLOWED_HTTPS_HOSTS is required in real mode")
            if self.pipeline_ca_bundle and not Path(self.pipeline_ca_bundle).is_file():
                raise ValueError("PIPELINE_CA_BUNDLE must identify a readable file")
            if self.pipeline_max_spool_bytes < self.pipeline_batch_target_bytes:
                raise ValueError(
                    "PIPELINE_MAX_SPOOL_BYTES must be at least PIPELINE_BATCH_TARGET_BYTES"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

#!/usr/bin/env bash

# Publish Data Mover to Posit Connect using values from a trusted .env file.
# rsconnect receives variable names only; it reads their values from this process.

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

env_file="${DATA_MOVER_ENV_FILE:-.env}"
if [[ "$env_file" != /* ]]; then
    env_file="$repo_root/$env_file"
fi
if [[ ! -f "$env_file" ]]; then
    printf 'Environment file not found: %s\n' "$env_file" >&2
    exit 2
fi

set -a
# shellcheck disable=SC1090
if ! . "$env_file"; then
    printf 'Could not load %s. Quote values containing spaces or shell characters.\n' "$env_file" >&2
    exit 2
fi
set +a

# Normalize aliases accepted for jwt-user-management compatibility. The published app receives
# the canonical names, avoiding an empty canonical setting masking a populated alias.
if [[ -z "${EMAIL_FROM:-}" && -n "${SMTP_FROM_EMAIL:-}" ]]; then
    export EMAIL_FROM="$SMTP_FROM_EMAIL"
fi
if [[ -z "${SMTP_STARTTLS:-}" && -n "${SMTP_USE_TLS:-}" ]]; then
    export SMTP_STARTTLS="$SMTP_USE_TLS"
fi
if [[ -z "${DIRECTORY_LOOKUP_TIMEOUT_SECONDS:-}" && -n "${DIRECTORY_LOOKUP_TIMEOUT_S:-}" ]]; then
    export DIRECTORY_LOOKUP_TIMEOUT_SECONDS="$DIRECTORY_LOOKUP_TIMEOUT_S"
fi
if [[ -z "${DIRECTORY_LOOKUP_VERIFY_TLS:-}" && -n "${DIRECTORY_LOOKUP_VERIFY_SSL:-}" ]]; then
    export DIRECTORY_LOOKUP_VERIFY_TLS="$DIRECTORY_LOOKUP_VERIFY_SSL"
fi

python_bin="${PYTHON_BIN:-$repo_root/.venv/bin/python}"
if [[ ! -x "$python_bin" ]]; then
    printf 'Python interpreter not found or not executable: %s\n' "$python_bin" >&2
    exit 2
fi

if command -v rsconnect >/dev/null 2>&1; then
    rsconnect_bin="$(command -v rsconnect)"
else
    rsconnect_bin="$repo_root/.venv/bin/rsconnect"
fi
if [[ ! -x "$rsconnect_bin" ]]; then
    printf 'rsconnect-python is not installed. Run: %s -m pip install rsconnect-python\n' "$python_bin" >&2
    exit 2
fi

connect_name="${CONNECT_NAME:-my-connect}"
connect_title="${CONNECT_TITLE:-Data Mover}"
requirements_file="${CONNECT_REQUIREMENTS_FILE:-requirements.txt}"

if [[ "${APP_ENV:-}" != "production" ]]; then
    printf 'APP_ENV must be production for Connect deployment.\n' >&2
    exit 2
fi
if [[ ! -r "$requirements_file" ]]; then
    printf 'Requirements file is not readable: %s\n' "$requirements_file" >&2
    exit 2
fi

printf 'Validating production configuration from %s\n' "$env_file"
"$python_bin" -m pip check
"$python_bin" -m app schema-status
"$python_bin" -c "from app.config import get_settings; get_settings(); print('Production configuration validates')"
"$python_bin" -m hedron build
test -f .hedron/build/manifest.json

environment_names=(
    APP_ENV
    APP_NAME
    CUSTOM_THEME_ENABLED
    PUBLIC_BASE_URL
    DATABASE_URL
    JWT_SECRET
    SESSION_PEPPER
    CSRF_SECRET
    API_TOKEN_ENCRYPTION_KEYS
    API_TOKEN_ACTIVE_KEY_ID
    API_TOKEN_MAX_WRAPS_PER_KEY
    JWT_ISSUER
    JWT_AUDIENCE
    AUTHENTICATION_MODE
    PASSWORD_ONLY_PRODUCTION_RISK_ACCEPTED
    TRUSTED_IDENTITY_HEADER
    ACCESS_TOKEN_MINUTES
    REFRESH_TOKEN_HOURS
    SESSION_IDLE_MINUTES
    COOKIE_SECURE
    COOKIE_PATH
    HSTS_INCLUDE_SUBDOMAINS
    TRUSTED_PROXY_IPS
    DB_POOL_SIZE
    DB_MAX_OVERFLOW
    DB_POOL_TIMEOUT
    DB_POOL_RECYCLE
    RATE_LIMIT_ENABLED
    RATE_LIMIT_WINDOW_SECONDS
    RATE_LIMIT_LOGIN_PER_SOURCE
    RATE_LIMIT_LOGIN_PER_ACCOUNT
    RATE_LIMIT_REGISTRATION_PER_SOURCE
    RATE_LIMIT_REGISTRATION_PER_ACCOUNT
    RATE_LIMIT_RESET_PER_SOURCE
    RATE_LIMIT_RESET_PER_ACCOUNT
    DIRECTORY_LOOKUP_URL
    DIRECTORY_LOOKUP_TIMEOUT_SECONDS
    DIRECTORY_LOOKUP_VERIFY_TLS
    DIRECTORY_LOOKUP_CA_BUNDLE
    DIRECTORY_LOOKUP_REQUIRED
    DIRECTORY_LOOKUP_BEARER_TOKEN
    ALLOWED_EMAIL_DOMAINS
    EMAIL_BACKEND
    EMAIL_REDACT_SENT_BODIES
    EMAIL_MAX_ATTEMPTS
    EMAIL_RETRY_BASE_SECONDS
    EMAIL_RETRY_MAX_SECONDS
    EMAIL_CLAIM_TIMEOUT_SECONDS
    EMAIL_FROM
    SMTP_HOST
    SMTP_PORT
    SMTP_STARTTLS
    SMTP_ALLOW_LEGACY_PORT25_FALLBACK
    SMTP_CA_BUNDLE
    SMTP_USERNAME
    SMTP_PASSWORD
    PASSWORD_HASH_SCHEME
    PBKDF2_ITERATIONS
    PASSWORD_BLOCKLIST_PATH
    DATA_MOVER_MODE
    PIPELINE_WORKER_ID
    PIPELINE_LEASE_SECONDS
    PIPELINE_BATCH_ROWS
    PIPELINE_BATCH_TARGET_BYTES
    PIPELINE_MAX_RUN_SECONDS
    PIPELINE_MAX_SOURCE_BYTES
    PIPELINE_MAX_SPOOL_BYTES
    PIPELINE_SPOOL_ROOT
    PIPELINE_HTTP_CONNECT_SECONDS
    PIPELINE_HTTP_READ_SECONDS
    PIPELINE_HTTP_WRITE_SECONDS
    PIPELINE_HTTP_RETRY_ATTEMPTS
    PIPELINE_CATALOG_TTL_SECONDS
    PIPELINE_CONNECTION_MAX_AGE_SECONDS
    PIPELINE_RUN_RETENTION_DAYS
    PIPELINE_EVENT_RETENTION_DAYS
    PIPELINE_ALLOWED_HTTPS_HOSTS
    PIPELINE_CA_BUNDLE
    PIPELINE_ENABLE_POSTGRES_WRITER
    PIPELINE_ENABLE_MSS_WRITER
    PIPELINE_ENABLE_MCSCOP_WRITER
    PIPELINE_APPLY_INTERNAL_CA_FIX
)

deploy_args=(
    deploy fastapi
    --name "$connect_name"
    --title "$connect_title"
    --entrypoint app.main:app
    --requirements-file "$requirements_file"
)
for name in "${environment_names[@]}"; do
    # Preserve explicitly empty values so an operator can clear a value retained by Connect.
    if [[ ${!name+x} == x ]]; then
        deploy_args+=(--environment "$name")
    fi
done
deploy_args+=(
    --exclude .env
    --exclude .venv
    --exclude '**/__pycache__/*'
    --exclude '**/*.db'
    --exclude '**/*.sqlite3'
    --exclude tests
    --exclude demo-app
    ./
)

printf 'Publishing %s to Connect server profile %s using %s\n' "$connect_title" "$connect_name" "$env_file"
exec "$rsconnect_bin" "${deploy_args[@]}"

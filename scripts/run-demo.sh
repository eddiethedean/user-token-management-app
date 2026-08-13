#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

PYTHON_BIN="${PYTHON_BIN:-python}"
DEMO_HOST="${DEMO_HOST:-127.0.0.1}"
DEMO_PORT="${DEMO_PORT:-8765}"
DEMO_PUBLIC_URL="${DEMO_PUBLIC_URL:-http://127.0.0.1:${DEMO_PORT}}"
DEMO_ADMIN_EMAIL="${DEMO_ADMIN_EMAIL:-demo@example.gov}"
DEMO_ADMIN_PASSWORD="${DEMO_ADMIN_PASSWORD:-Orbit-Copper!Trail-47}"

export APP_ENV="development"
export APP_NAME="Data Mover"
export PUBLIC_BASE_URL="${DEMO_PUBLIC_URL}"
export DATABASE_URL="${DEMO_DATABASE_URL:-sqlite:///./data-mover-demo.db}"
export JWT_SECRET="${JWT_SECRET:-data-mover-local-demo-jwt-secret-2026-08-13}"
export SESSION_PEPPER="${SESSION_PEPPER:-data-mover-local-demo-session-pepper-2026-08-13}"
export CSRF_SECRET="${CSRF_SECRET:-data-mover-local-demo-csrf-secret-2026-08-13}"
if [[ -z "${API_TOKEN_ENCRYPTION_KEYS:-}" ]]; then
  export API_TOKEN_ENCRYPTION_KEYS='{"development-v1":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}'
fi
export API_TOKEN_ACTIVE_KEY_ID="${API_TOKEN_ACTIVE_KEY_ID:-development-v1}"
export AUTHENTICATION_MODE="local_password"
export COOKIE_SECURE="false"
export COOKIE_PATH="auto"
export ALLOWED_EMAIL_DOMAINS="${ALLOWED_EMAIL_DOMAINS:-example.gov,example.mil,socom.mil}"
export EMAIL_BACKEND="console"
export EMAIL_FROM="${EMAIL_FROM:-Data Mover <no-reply@example.gov>}"

"${PYTHON_BIN}" -m app migrate
ADMIN_BOOTSTRAP_PASSWORD="${DEMO_ADMIN_PASSWORD}" \
  "${PYTHON_BIN}" -m app create-admin \
  --email "${DEMO_ADMIN_EMAIL}" \
  --password-env ADMIN_BOOTSTRAP_PASSWORD
"${PYTHON_BIN}" -m app seed-demo-connections --email "${DEMO_ADMIN_EMAIL}"

printf '\nData Mover demo: %s/login\n' "${DEMO_PUBLIC_URL}"
printf 'Email: %s\n' "${DEMO_ADMIN_EMAIL}"
printf 'Password: %s\n\n' "${DEMO_ADMIN_PASSWORD}"

exec "${PYTHON_BIN}" -m app serve --host "${DEMO_HOST}" --port "${DEMO_PORT}"

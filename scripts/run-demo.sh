#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  : "${PYTHON_BIN}"
elif [[ -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
  PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"
else
  PYTHON_BIN="python"
fi
DEMO_HOST="${DEMO_HOST:-127.0.0.1}"
DEMO_PORT="${DEMO_PORT:-8765}"
DEMO_PUBLIC_URL="${DEMO_PUBLIC_URL:-}"
# Prefer any public base already supplied by current or future Hedron launchers.
# The app-side discovery below is only a fallback for Workbench releases that
# omit the runtime indicator tracked in Hedron #881.
DEMO_WORKBENCH_URL="${HEDRON_WORKBENCH_PUBLIC_BASE_URL:-${FASTAPI_WORKBENCH_PUBLIC_BASE_URL:-${HEDRON_WORKBENCH_RESOLVED_PUBLIC_BASE:-${FASTAPI_WORKBENCH_RESOLVED_PUBLIC_BASE:-}}}}"
DEMO_REQUEST_DISCOVERY="false"
DEMO_ADMIN_EMAIL="${DEMO_ADMIN_EMAIL:-demo@example.gov}"
DEMO_ADMIN_PASSWORD="${DEMO_ADMIN_PASSWORD:-Orbit-Copper!Trail-47}"

# Ask Workbench for the session URL of the exact port this script will bind.
# Passing that URL back to Hedron keeps forms, cookies, and redirects on the
# same /s/.../p/... mount instead of falling through to a stale /proxy/8000/.
if [[ -z "${DEMO_PUBLIC_URL}" && -z "${DEMO_WORKBENCH_URL}" ]] && \
  command -v rserver-url >/dev/null 2>&1; then
  DEMO_REQUEST_DISCOVERY="true"
  DISCOVERED_WORKBENCH_URL="$(rserver-url -l "${DEMO_PORT}" 2>/dev/null || true)"
  case "${DISCOVERED_WORKBENCH_URL}" in
    http://* | https://*)
      DEMO_WORKBENCH_URL="${DISCOVERED_WORKBENCH_URL%/}"
      ;;
  esac
fi

if [[ -n "${DEMO_WORKBENCH_URL}" ]]; then
  DEMO_WORKBENCH_URL="${DEMO_WORKBENCH_URL%/}"
  export HEDRON_WORKBENCH_PUBLIC_BASE_URL="${DEMO_WORKBENCH_URL}"
  DEMO_PUBLIC_URL="${DEMO_PUBLIC_URL:-${DEMO_WORKBENCH_URL}}"
fi
DEMO_PUBLIC_URL="${DEMO_PUBLIC_URL:-http://127.0.0.1:${DEMO_PORT}}"
DEMO_PUBLIC_URL="${DEMO_PUBLIC_URL%/}"

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

if [[ -n "${DEMO_WORKBENCH_URL}" ]]; then
  printf '\nData Mover demo (open this Workbench URL): %s/login\n' "${DEMO_PUBLIC_URL}"
else
  printf '\nData Mover demo: %s/login\n' "${DEMO_PUBLIC_URL}"
fi
printf 'Email: %s\n' "${DEMO_ADMIN_EMAIL}"
printf 'Password: %s\n\n' "${DEMO_ADMIN_PASSWORD}"

SERVE_ARGS=(-m app serve --host "${DEMO_HOST}" --port "${DEMO_PORT}")
if [[ "${DEMO_REQUEST_DISCOVERY}" == "true" ]]; then
  SERVE_ARGS+=(--discover)
fi
exec "${PYTHON_BIN}" "${SERVE_ARGS[@]}"

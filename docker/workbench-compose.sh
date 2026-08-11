#!/usr/bin/env bash
# Run docker compose against the Workbench stack using the project .env.
# Does not `source` .env (values may contain spaces); compose --env-file parses it.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT}/docker/compose.workbench.yml"
ENV_FILE="${ROOT}/.env"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}. Copy .env.example and set POSIT_WORKBENCH_KEY." >&2
  exit 1
fi

if ! grep -Eq '^POSIT_WORKBENCH_KEY=.+' "${ENV_FILE}"; then
  echo "POSIT_WORKBENCH_KEY is missing or empty in .env" >&2
  exit 1
fi

export PWB_IMAGE="${PWB_IMAGE:-posit/workbench:latest}"
export PWB_HOST_PORT="${PWB_HOST_PORT:-8787}"
export APP_HOST_PORT="${APP_HOST_PORT:-8000}"
export REWRITE_HOST_PORT="${REWRITE_HOST_PORT:-8788}"
export PWB_TESTUSER="${PWB_TESTUSER:-posit}"
export PWB_TESTUSER_PASSWD="${PWB_TESTUSER_PASSWD:-Xk9#mQ2\$vL8!nR4p}"
export PWB_LAUNCHER="${PWB_LAUNCHER:-false}"

cd "${ROOT}"
exec docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" "$@"

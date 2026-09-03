#!/usr/bin/env bash
# Run one Data Mover process from a Posit Workbench checkout.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

env_file="${DATA_MOVER_ENV_FILE:-.env}"
if [[ "${env_file}" != /* ]]; then
  env_file="${PROJECT_ROOT}/${env_file}"
fi
if [[ ! -r "${env_file}" ]]; then
  printf 'Missing readable environment file: %s\n' "${env_file}" >&2
  printf 'Create .env from .env.example, then try again.\n' >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
. "${env_file}"
set +a

python_bin="${PYTHON_BIN:-${PROJECT_ROOT}/.venv/bin/python}"
if [[ ! -x "${python_bin}" ]]; then
  printf 'Python interpreter not found: %s\n' "${python_bin}" >&2
  printf 'Create the environment with: python3.11 -m venv .venv\n' >&2
  exit 2
fi

role="${1:-web}"
shift || true

case "${role}" in
  web|migrate|admin) ;;
  *)
    printf 'Usage: %s [web|migrate|admin]\n' "$(basename "$0")" >&2
    exit 2
    ;;
esac

workbench_url=""
if [[ "${DATA_MOVER_MODE:-demo}" == "real" ]]; then
  port="${WORKBENCH_PORT:-8765}"
  if command -v rserver-url >/dev/null 2>&1; then
    workbench_url="$(rserver-url -l "${port}" 2>/dev/null || true)"
  elif [[ -x /usr/lib/rstudio-server/bin/rserver-url ]]; then
    workbench_url="$(/usr/lib/rstudio-server/bin/rserver-url -l "${port}" 2>/dev/null || true)"
  else
    printf 'Workbench rserver-url helper not found. Add it to PATH and try again.\n' >&2
    exit 2
  fi
  if [[ -z "${workbench_url}" ]]; then
    printf 'Could not determine the Workbench URL for port %s.\n' "${port}" >&2
    exit 2
  fi
  case "${workbench_url}" in
    http://*|https://*) ;;
    *) printf 'rserver-url returned an invalid URL: %s\n' "${workbench_url}" >&2; exit 2 ;;
  esac
  # A new session must replace every inherited runtime mount. Hedron receives
  # the current URL as the sole explicit handoff and derives the mount from it.
  unset UVICORN_ROOT_PATH HEDRON_WORKBENCH_MOUNT FASTAPI_WORKBENCH_MOUNT
  unset HEDRON_WORKBENCH_RESOLVED_MOUNT FASTAPI_WORKBENCH_RESOLVED_MOUNT
  unset HEDRON_ROOT_PATH FASTAPI_WORKBENCH_ROOT_PATH
  unset FASTAPI_WORKBENCH_PUBLIC_BASE_URL HEDRON_WORKBENCH_RESOLVED_PUBLIC_BASE
  unset FASTAPI_WORKBENCH_RESOLVED_PUBLIC_BASE
  export PUBLIC_BASE_URL="${workbench_url%/}"
  export HEDRON_WORKBENCH_PUBLIC_BASE_URL="${PUBLIC_BASE_URL}"
fi

case "${role}" in
  web)
    port="${WORKBENCH_PORT:-8765}"
    printf 'Starting Data Mover on Workbench port %s\n' "${port}"
    [[ -n "${PUBLIC_BASE_URL:-}" ]] && printf 'Open: %s/login\n' "${PUBLIC_BASE_URL%/}"
    serve_args=(-m app serve --host 127.0.0.1 --port "${port}")
    [[ "${DATA_MOVER_MODE:-demo}" == "real" ]] && serve_args+=(--discover)
    exec "${python_bin}" "${serve_args[@]}" "$@"
    ;;
  migrate)
    "${python_bin}" -m app migrate
    "${python_bin}" -m app schema-status
    ;;
  admin)
    exec "${python_bin}" -m app create-admin "$@"
    ;;
esac

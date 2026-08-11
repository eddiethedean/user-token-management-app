#!/bin/sh
set -eu

python -m app migrate

if [ -n "${ADMIN_EMAIL:-}" ] && [ -n "${ADMIN_BOOTSTRAP_PASSWORD:-}" ]; then
  python -m app create-admin --email "${ADMIN_EMAIL}" --password-env ADMIN_BOOTSTRAP_PASSWORD
fi

exec python -m app serve --host 0.0.0.0 --port "${PORT:-8000}"

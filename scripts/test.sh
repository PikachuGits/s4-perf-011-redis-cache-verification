#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

export TEST_DATABASE_URL="${TEST_DATABASE_URL:-postgresql+psycopg://clawhunt@127.0.0.1:55432/clawhunt_2984_test}"
export REDIS_URL="${REDIS_URL:-redis://127.0.0.1:56379/15}"
export ADMIN_TOKEN="${ADMIN_TOKEN:-test-admin-token}"

exec .venv/bin/python -m pytest "$@"

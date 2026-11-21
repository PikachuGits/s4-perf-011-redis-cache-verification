#!/usr/bin/env bash
set -euo pipefail
BASE_URL="${1:-http://127.0.0.1:8011}"
ADMIN_TOKEN="${ADMIN_TOKEN:-dev-admin-token}"

echo "== healthz"
curl -s "$BASE_URL/healthz"
echo

echo "== four cache points"
curl -s -H "X-Admin-Token: $ADMIN_TOKEN" "$BASE_URL/api/admin/cache/points" \
  | .venv/bin/python -c 'import json,sys; d=json.load(sys.stdin); print([(p["code"],p["pattern"],p["ttl_seconds"]) for p in d["points"]]); print([(p["code"],p["pattern"],p["ttl_seconds"]) for p in d["auxiliary"]])'
echo

for path in \
  "/api/tax/dicts/industry/tree" \
  "/api/site/home" \
  "/api/dashboard/metrics" \
  "/api/iam/permissions"; do
  echo "== $path (first / second)"
  curl -s -D - -o /dev/null "$BASE_URL$path" | grep -Ei 'HTTP/|x-cache|x-db|x-response' || true
  curl -s -D - -o /dev/null "$BASE_URL$path" | grep -Ei 'HTTP/|x-cache|x-db|x-response' || true
  echo
done

echo "== unknown dictionary item (bloom guard)"
curl -s -D - "$BASE_URL/api/tax/dicts/industry/items/NOT-EXIST-SMOKE" -o /tmp/msp-smoke-body.json \
  | grep -Ei 'HTTP/|x-cache|x-db' || true
cat /tmp/msp-smoke-body.json
echo

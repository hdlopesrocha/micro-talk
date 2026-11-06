#!/usr/bin/env bash
# Quick live-stack health check. This does not download models or run a
# workflow. For the optional end-to-end test see tests/integration/.
set -euo pipefail

BASE_URL="${MICROTALK_URL:-http://localhost:8080}"
TOKEN="${GATEWAY_AUTH_TOKEN:-}"
AUTH_HEADER=()
if [[ -n "$TOKEN" ]]; then
  AUTH_HEADER=(-H "Authorization: Bearer $TOKEN")
fi

echo "==> Proxy health: $BASE_URL/healthz"
curl -fsS "$BASE_URL/healthz" >/dev/null

echo "==> Gateway health"
curl -fsS "${AUTH_HEADER[@]}" "$BASE_URL/api/health"

echo
echo "==> Auth status"
curl -fsS "$BASE_URL/api/v1/auth/status"

echo
echo "==> Service catalog"
curl -fsS "${AUTH_HEADER[@]}" "$BASE_URL/api/v1/catalog" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps({"health": d.get("health"), "translation": d.get("translation_status", {}).get("supported_pairs"), "summary": d.get("summary_status", {}).get("available"), "voices": [(v["id"], v["available"]) for v in d.get("tts", {}).get("voices", [])]}, indent=2))'

echo
echo "Smoke check passed. Models still need the explicit download command before workflows can run."

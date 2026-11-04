#!/usr/bin/env bash
# Smoke test of the compose stack (CI and the demo PC):
#   S1: sample files dropped into data/inbox reach the registry change feed,
#   S4: a damper fault from the gateway becomes a work order,
# with tokens issued by Keycloak (client credentials). Run after
#   docker compose up -d --build --wait
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

psql() { docker compose exec -T postgres psql -U postgres -d registry -tAc "$1"; }
wait_for() {  # wait_for <description> <command...>
  local what=$1; shift
  for _ in $(seq 1 90); do
    if "$@" >/dev/null 2>&1; then echo "ok: $what"; return 0; fi
    sleep 2
  done
  echo "FAILED: $what"; docker compose ps; docker compose logs --tail 80; exit 1
}

token=$(curl -fsS -u "svc-field-app:${OIDC_SECRET_SVC_FIELD_APP}" -d grant_type=client_credentials \
  http://localhost:8080/realms/vigiles/protocol/openid-connect/token | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")
echo "ok: client credentials token from Keycloak"

cp data/samples/zestawienie_B1.csv data/inbox/
wait_for "asset list imported" test "$(psql "select count(*) from assets")" -ge 11
cp data/samples/site-config.json data/inbox/
wait_for "device catalogue imported" test "$(psql "select count(*) from devices")" -eq 3

wait_for "assets in the change feed (JWT accepted)" sh -c "[ \$(curl -fsS -H 'Authorization: Bearer $token' 'http://localhost:8001/api/v1/sync/changes?since=0&limit=1000' | grep -o '\"entity\":\"asset\"' | wc -l) -ge 11 ]"
test "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8001/api/v1/sync/changes)" = 401 && echo "ok: request without token -> 401"

curl -fsS -X POST -H "X-Api-Key: ${GATEWAY_API_KEY}" -H "Content-Type: application/json" \
  -d '{"device_id": "BETA-7F3K2", "closing_time_s": 95.2}' http://localhost:8005/scenario/damper-fault >/dev/null
wait_for "device fault -> work order through MQTT, hub and registry" \
  test "$(psql "select count(*) from work_orders where reason = 'usterka_urzadzenia'")" -eq 1
wait_for "work order delivered to the field app" sh -c \
  "docker compose exec -T field-app python -c \"import sqlite3,sys; c=sqlite3.connect('/var/lib/vigiles/field_app.sqlite'); sys.exit(0 if c.execute('select count(*) from tasks').fetchone()[0] >= 1 else 1)\""
echo "compose smoke test passed"

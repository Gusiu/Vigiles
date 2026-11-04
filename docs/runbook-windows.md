# Runbook: Windows PC with Docker Desktop

## Prerequisites

- Windows 10/11 with WSL2, Docker Desktop (WSL2 backend), Git, Python 3.12
- optional for the video: OBS Studio, MQTT Explorer
- Git must keep LF line endings in shell scripts; `.gitattributes` takes
  care of it (`*.sh text eol=lf`)

## First start

```powershell
git clone https://github.com/Gusiu/Vigiles.git
```

```powershell
cd Vigiles
```

```powershell
python scripts/gen_secrets.py
```

```powershell
docker compose up -d --build --wait
```

`gen_secrets.py` writes `.env` with random passwords and renders
`infra/keycloak/realm.json`. The first build and the Keycloak start take a
few minutes. `--wait` returns when every service is healthy:

```powershell
docker compose ps
```

## Addresses

| Service | Address | Log in as |
|---|---|---|
| Registry UI | http://localhost:8001 | `anna.kierownik` |
| Field app | http://localhost:8002 | `jan.technik` |
| Hub console | http://localhost:8003, http://localhost:8004 | `anna.kierownik` (console), `admin` (dead letters) |
| Keycloak | http://localhost:8080 | admin console: `admin` / `KEYCLOAK_ADMIN_PASSWORD` |
| Gateway scenarios | http://localhost:8005 (localhost only, header `X-Api-Key: GATEWAY_API_KEY`) | - |
| MQTT | localhost:1883 | `explorer` / `MQTT_EXPLORER_PASSWORD` |

The password of the demo users is `DEMO_USER_PASSWORD` in `.env`.

## Scenarios

Import (S1):

```powershell
copy data\samples\zestawienie_B1.csv data\inbox\
```

```powershell
copy data\samples\zestawienie_B1_bledy.csv data\inbox\
```

```powershell
copy data\samples\site-config.json data\inbox\
```

Device fault (S4), alarm coincidence (S5), contamination (S6), unknown
device (S7):

```powershell
$h = @{ "X-Api-Key" = (Select-String -Path .env -Pattern "^GATEWAY_API_KEY=(.*)").Matches.Groups[1].Value }
```

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8005/scenario/damper-fault -Headers $h -ContentType application/json -Body '{"device_id": "BETA-7F3K2", "closing_time_s": 95.2}'
```

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8005/scenario/alarm -Headers $h -ContentType application/json -Body '{"zone": "Z3", "detectors": 2, "interval_s": 10}'
```

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8005/scenario/contamination -Headers $h -ContentType application/json -Body '{"device_id": "A-000124", "value": 85}'
```

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8005/scenario/unknown-device -Headers $h -ContentType application/json -Body '{}'
```

For S7 then import `data\samples\site-config-v2.json` and replay the dead
letter in the hub console as `admin`.

## Failures

```powershell
docker compose stop registry
```

```powershell
docker compose start registry
```

```powershell
docker compose kill hub-1
```

```powershell
docker compose stop mosquitto
```

```powershell
docker compose kill gateway
```

Use `kill` for the gateway: a graceful `stop` disconnects cleanly and
the broker does not publish the Last Will.

## Following one flow

```powershell
docker compose logs --no-log-prefix | Select-String "<correlation_id>"
```

## Smoke test and reset

```powershell
wsl bash scripts/compose_smoke.sh
```

```powershell
docker compose down -v
```

`down -v` deletes all data (databases, broker, field app, gateway buffer).
After `gen_secrets.py --force` the volumes must be reset this way, because
the database passwords are set only when the volume is created.

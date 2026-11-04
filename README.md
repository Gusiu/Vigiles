# Vigiles

Monitoring and maintenance platform for the fire protection of a building
(fire stopping of service penetrations, fire doors and dampers, smoke
detectors). Coursework for *Systems Integration*: each grade adds an
integration style from Hohpe and Woolf, *Enterprise Integration Patterns*.
The name comes from the *vigiles*, the fire brigade and night watch of
ancient Rome established by Augustus in 6 AD.

> Vigiles is a supervision and maintenance layer. It does not replace a
> certified fire alarm control panel: in a real building alarms are
> handled by a panel compliant with EN 54-2.

| Grade | Integration style | Tag |
|---|---|---|
| 3.0 | File Transfer: CSV/JSON importer -> PostgreSQL | `v3.0` |
| 4.0 | Remote Procedure Invocation: two-way REST sync between the registry and the field app | `v4.0` |
| 5.0 | Messaging: MQTT, integration hub with EIP patterns, Keycloak, resilience | `v5.0` |

The specification is [`PLAN.md`](PLAN.md).

## What it does

- **3.0** A contractor drops an asset list (CSV from Excel, cp1250 or
  UTF-8, `;` or `,`) and a site configuration (JSON) into `data/inbox`.
  The importer validates every row, loads valid rows in one transaction,
  reports rejected rows with reasons and leaves files in the inbox while
  the database is down. [docs/import.md](docs/import.md)
- **4.0** The registry (System B) and the field app (System A) share no
  database. A webhook makes the field app pull the change feed; inspection
  results are pushed back with their base revision and merged per field;
  concurrent edits of `notes` become conflicts resolved by a manager. The
  field app works offline. [docs/sync-protocol.md](docs/sync-protocol.md)
- **5.0** An IoT gateway simulates devices of two vendors and publishes to
  Mosquitto. Two hub replicas normalise, enrich and route the messages,
  aggregate alarm coincidence, and create work orders and incidents in the
  registry through an outbox; invalid messages go to a dead letter channel
  with replay. Keycloak issues OAuth2 tokens; every service enforces
  roles. Every failure scenario has a test.
  [docs/architecture.md](docs/architecture.md),
  [docs/patterns.md](docs/patterns.md),
  [docs/resilience.md](docs/resilience.md),
  [docs/security.md](docs/security.md)

## Running with Docker (demo)

```bash
python scripts/gen_secrets.py
```

```bash
docker compose up -d --build --wait
```

```bash
scripts/compose_smoke.sh
```

Registry http://localhost:8001, field app http://localhost:8002, hub
consoles http://localhost:8003 and :8004, Keycloak http://localhost:8080.
Users and scenario commands: [docs/runbook-windows.md](docs/runbook-windows.md).
Video script: [docs/video-script.md](docs/video-script.md).

## Running without Docker

For environments where container images cannot be downloaded (Ubuntu
24.04 with apt, Python 3.12, [uv](https://docs.astral.sh/uv/)).
Authentication uses `tools/dev_issuer` instead of Keycloak.

```bash
python3 scripts/local_up.py
```

```bash
uv sync
```

```bash
uv run python scripts/supervisor.py up
```

```bash
cp data/samples/zestawienie_B1.csv data/samples/site-config.json data/inbox/
```

`local_up.py` installs PostgreSQL 16 and Mosquitto 2 when missing, starts
PostgreSQL and applies `infra/postgres/init.sql`. `supervisor.py` runs the
dev issuer, broker, registry, field app, both hub replicas, gateway and
importer from `local-nodes.yaml` (`status`, `stop <node>`, `start <node>`,
`kill <node>`, `down`); logs in `.local/logs/`.

## Tests

```bash
uv run pytest -m unit
```

```bash
uv run pytest -m component
```

```bash
uv run pytest -m "e2e or chaos"
```

| Level | Scope | Needs |
|---|---|---|
| unit | CSV parsing, translators, router, aggregator rules, merge, retry, JWT validation | nothing |
| component | schema and triggers, import, push/pull, conflicts, idempotency, outbox with failing HTTP, hub with a real broker, gateway buffer, broker ACL, RBAC matrix, OIDC login | PostgreSQL 16, Mosquitto 2 |
| e2e | scenarios S1-S7 | the local stack, started by the tests |
| chaos | failures C1-C7 | the local stack + `NodeController` |

CI (`.github/workflows/ci.yml`) runs all levels and a compose smoke test
with Keycloak.

## Repository layout

```
libs/common/        settings, JSON logging, correlation ID, problem+json, health, retry,
                    migrations, auth (static, JWT), outbox relay, MQTT, envelope, shared UI
services/registry/  System B: API, sync, conflicts, notifier, UI, migrations
services/field_app/ System A: SQLite, translator, sync engine, UI
services/hub/       ingress (translators, enricher, router), consumers, DLQ, console
services/gateway/   device simulator, store-and-forward buffer, scenario API
tools/importer/     file transfer importer
tools/dev_issuer/   token issuer for local mode and tests only
infra/              postgres, mosquitto, keycloak
data/samples/       sample input files; inbox/, archive/, error/ are working folders
scripts/            local_up, supervisor, run_mosquitto, gen_secrets, export_openapi, compose_smoke
tests/              unit/, component/, e2e/, chaos/, harness/
docs/               architecture, patterns, import, sync-protocol, resilience, security,
                    adr/, api/, asyncapi.yaml, runbook-windows, video-script
```

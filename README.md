# Vigiles

Monitoring and maintenance platform for the fire protection of a building
(fire stopping of service penetrations, fire doors and dampers, smoke
detectors). Coursework for *Systems Integration*: each grade adds an
integration style from Hohpe and Woolf, *Enterprise Integration Patterns*.

The name comes from the *vigiles*, the fire brigade and night watch of
ancient Rome established by Augustus in 6 AD.

> Vigiles is a maintenance and supervision layer. It does not replace a
> certified fire alarm control panel: in a real building alarms are
> handled by a panel compliant with EN 54-2.

| Grade | Integration style | Status |
|---|---|---|
| 3.0 | File Transfer: CSV/JSON importer -> PostgreSQL | done (`v3.0`) |
| 4.0 | Remote Procedure Invocation: two-way REST sync between the Registry and the field app | done (`v4.0`) |
| 5.0 | Messaging: MQTT, integration hub with EIP patterns, Keycloak, resilience | planned |

The full specification is in [`PLAN.md`](PLAN.md).

## What works in 3.0

A contractor drops an asset list (CSV exported from Excel) and a site
configuration (JSON) into `data/inbox`. The importer validates them,
loads valid rows into the Registry database in one transaction, writes
rejected rows with reasons to `data/error`, and archives the file. It runs
on demand (`vigiles-import run`) or polls the inbox every 10 s
(`vigiles-import watch`). If the database is down the file stays in the
inbox and is imported in the next cycle. Details: [`docs/import.md`](docs/import.md).

## What works in 4.0

The Registry (System B, PostgreSQL) and the field app (System A, SQLite)
synchronise both ways over REST. A manager creates a work order in the
Registry UI (http://localhost:8001); a webhook makes the field app
(http://localhost:8002) pull the change feed within seconds; the
technician records the inspection, which goes back to the Registry and
closes the order. Concurrent edits are merged field by field with field
ownership; a concurrent edit of `notes` becomes a conflict a manager
resolves. The field app keeps working offline and when the Registry is
down. Services authenticate with bearer tokens. Details:
[`docs/sync-protocol.md`](docs/sync-protocol.md).

## Running with Docker (demo)

```bash
cp .env.example .env
```

Change the passwords in `.env`, then:

```bash
docker compose up -d --build
```

```bash
cp data/samples/zestawienie_B1.csv data/samples/zestawienie_B1_bledy.csv data/samples/site-config.json data/inbox/
```

```bash
docker compose logs -f importer
```

Imported files appear in `data/archive`, the report of rejected rows in
`data/error/zestawienie_B1_bledy.rejected.csv`. To look at the data:

```bash
docker compose exec postgres psql -U postgres -d registry -c "select code, asset_type, fire_rating, rev from assets"
```

Registry UI: http://localhost:8001 (log in as `anna.kierownik`), field
app: http://localhost:8002 (log in as `jan.technik`).

## Running without Docker

For environments where container images cannot be downloaded. Requires
Ubuntu 24.04 with apt, Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
python3 scripts/local_up.py
```

```bash
uv sync
```

```bash
uv run vigiles-registry migrate
```

```bash
uv run python scripts/supervisor.py up
```

```bash
cp data/samples/zestawienie_B1.csv data/inbox/
```

`supervisor.py` runs the registry (8001), the field app (8002) and the
importer as local processes (`status`, `stop <node>`, `start <node>`,
`kill <node>`, `down`); logs are in `.local/logs/`.

`local_up.py` installs PostgreSQL 16 and Mosquitto 2 when missing, starts
them, creates `.env` from `.env.example` if needed and applies
`infra/postgres/init.sql`. It is safe to run repeatedly.

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

Component tests need PostgreSQL with the project roles (`local_up.py`);
each test gets a throwaway database. E2e and chaos tests start the
registry, the field app and the importer as real processes on ports
18001/18002 and stop or kill them through `NodeController`. CI
(`.github/workflows/ci.yml`) runs all levels and a compose smoke test.

## Repository layout

```
libs/common/       settings, JSON logging, correlation ID, problem+json, health, retry,
                   migrations, auth, outbox relay, shared UI
services/registry/ System B: API, sync, conflicts, UI, migrations
services/field_app/ System A: SQLite, translator, sync engine, UI
tools/importer/    file transfer importer
infra/postgres/    databases and roles
data/samples/      sample input files; inbox/, archive/, error/ are working folders
scripts/           local_up.py (infrastructure), supervisor.py (processes) without Docker
tests/             unit/, component/, e2e/, chaos/, harness/
docs/              documentation
```

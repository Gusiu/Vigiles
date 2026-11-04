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
| 4.0 | Remote Procedure Invocation: two-way REST sync between the Registry and the field app | planned |
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
uv run vigiles-import run data/samples/zestawienie_B1.csv
```

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

Component tests need PostgreSQL with the project roles (`local_up.py`);
each test gets a throwaway database. CI (`.github/workflows/ci.yml`) runs
both levels and a compose smoke test that imports the samples through the
running containers.

## Repository layout

```
libs/common/       settings, JSON logging, correlation ID, problem+json, health, retry, migrations
services/registry/ System B: schema and migrations (API from 4.0)
tools/importer/    file transfer importer
infra/postgres/    databases and roles
data/samples/      sample input files; inbox/, archive/, error/ are working folders
scripts/           local_up.py - local infrastructure without Docker
tests/             unit/, component/, harness/
docs/              documentation
```

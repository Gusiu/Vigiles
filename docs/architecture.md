# Architecture

Vigiles supervises the fire protection of a building: fire stopping of
service penetrations, fire doors and dampers, smoke detectors. Every grade
of the course adds one integration style from Hohpe and Woolf,
*Enterprise Integration Patterns* (ch. 2):

| Grade | Style | Where |
|---|---|---|
| 3.0 | File Transfer | `tools/importer`: CSV/JSON from `data/inbox` -> PostgreSQL ([import.md](import.md)) |
| 4.0 | Remote Procedure Invocation | REST sync registry <-> field app ([sync-protocol.md](sync-protocol.md)) |
| 5.0 | Messaging | MQTT, integration hub with EIP patterns ([patterns.md](patterns.md)) |

Shared Database is deliberately not used between the two systems (ADR-007).

> Vigiles is a supervision and maintenance layer. It does not replace a
> certified fire alarm control panel: in a real building alarms are
> handled by a panel compliant with EN 54-2. The alarm coincidence in the
> hub demonstrates the Aggregator pattern.

## C4: system context

```mermaid
flowchart TB
  contractor["Contractor<br/>(delivers asset lists and site configuration)"]
  manager["Manager<br/>(work orders, conflicts)"]
  technician["Technician<br/>(inspections, also offline)"]
  owner["Building manager<br/>(read only)"]
  devices["Devices in the building<br/>(detectors, damper controllers of two vendors)"]
  vigiles["Vigiles<br/>supervision and maintenance platform"]
  idp["Keycloak<br/>identity provider (OAuth2/OIDC)"]
  panel["Fire alarm control panel (EN 54-2)<br/>out of scope"]
  contractor -->|"CSV, JSON files"| vigiles
  manager --> vigiles
  technician --> vigiles
  owner --> vigiles
  devices -->|"MQTT via IoT gateway"| vigiles
  vigiles -->|"tokens, login"| idp
  devices -.->|"real alarms"| panel
```

## C4: containers

```mermaid
flowchart TB
  subgraph EDGE["Building"]
    GW["gateway<br/>Python, paho<br/>store-and-forward SQLite, Last Will"]
  end
  subgraph BUS["Integration layer (5.0)"]
    MQ[("Mosquitto 2<br/>MQTT 5, ACL, persistence")]
    H1["hub-1<br/>FastAPI + paho"]
    H2["hub-2<br/>FastAPI + paho"]
    HDB[("PostgreSQL: hub<br/>processed_messages, alarm_windows,<br/>outbox, dead_letters")]
  end
  subgraph FILES["Files (3.0)"]
    INBOX["data/inbox<br/>CSV, JSON"]
    IMP["importer<br/>Python CLI"]
  end
  subgraph SYSB["System B: registry"]
    REG["registry<br/>FastAPI API + Jinja2/htmx UI"]
    RDB[("PostgreSQL: registry<br/>history, change feed, conflicts, outbox")]
  end
  subgraph SYSA["System A: field app"]
    FA["field-app<br/>FastAPI API + Jinja2/htmx UI"]
    FDB[("SQLite<br/>own model, pending_changes")]
  end
  KC["Keycloak<br/>OAuth2 / OIDC"]

  GW -->|"vigiles/raw, QoS 1"| MQ
  MQ -->|"$share subscriptions"| H1 & H2
  H1 & H2 -->|"vigiles/events/*"| MQ
  H1 & H2 --- HDB
  H1 & H2 -->|"REST + outbox, Idempotency-Key"| REG
  INBOX --> IMP -->|"one transaction per file"| RDB
  REG --- RDB
  REG -->|"webhook doorbell (outbox)"| FA
  FA <-->|"GET /sync/changes, POST /sync/push"| REG
  FA --- FDB
  KC -.->|"JWT"| REG & FA & H1 & H2
```

| Container | Responsibility | State | Port |
|---|---|---|---|
| `importer` | loads files from `data/inbox` into the registry | `import_runs` | - |
| `registry` (System B) | assets, work orders, inspections, incidents; arbiter of conflicts | PostgreSQL `registry` | 8001 |
| `field-app` (System A) | technician app, offline work, sync engine | SQLite | 8002 |
| `mosquitto` | channels, durable sessions, ACL | volume | 1883 |
| `gateway` | simulated devices of vendors Alfa and Beta, buffer, Last Will, scenario API | SQLite buffer | 8005 (localhost) |
| `hub-1`, `hub-2` | normalise, enrich, route, aggregate, dead letters, outbox, console | PostgreSQL `hub` | 8003, 8004 |
| `keycloak` | identity provider | dev mode | 8080 |
| `postgres` | databases `registry` and `hub`, separate roles | volume | 5432 |

In local mode (no Docker) `tools/dev_issuer` (8006) stands in for
Keycloak and `scripts/supervisor.py` runs the same processes.

## Sequence S2: work order and inspection (4.0)

```mermaid
sequenceDiagram
  autonumber
  participant M as manager
  participant R as registry
  participant F as field app
  participant T as technician
  M->>R: create work order (UI)
  R->>R: trigger: rev+1, entity_history, change_log
  R->>R: notifier: outbox row (doorbell)
  R->>F: POST /webhooks/registry (JWT svc-registry)
  F->>R: GET /sync/changes?since=cursor (JWT svc-field-app)
  F->>F: translate, store task, move cursor
  T->>F: start, inspection "needs repair"
  F->>F: SQLite + 3 pending changes in one transaction
  F->>R: POST /sync/push (base_rev, change_id)
  R->>R: three-way merge, applied_changes
  R-->>F: applied (new_rev)
  Note over R: inspection saved, asset do_naprawy, order zakonczone
```

## Sequence S4: device fault to work order (5.0)

```mermaid
sequenceDiagram
  autonumber
  participant GW as gateway
  participant MQ as Mosquitto
  participant H as hub-1 or hub-2
  participant R as registry
  participant F as field app
  GW->>GW: SQLite buffer
  GW->>MQ: vigiles/raw/beta/gw-01/BETA-7F3K2 (QoS 1)
  MQ-->>GW: PUBACK, row deleted from the buffer
  MQ->>H: $share/ingress
  H->>H: Normalizer, Content Enricher, Content-Based Router
  H->>MQ: vigiles/events/fault/B1/Z3, wait for PUBACK
  H-->>MQ: ack of the raw message
  MQ->>H: $share/faults
  H->>H: processed_messages + outbox row in one transaction, then ack
  H->>R: POST /work-orders (JWT svc-hub, Idempotency-Key, X-Correlation-ID)
  R->>R: trigger: rev+1, entity_history, change_log
  R->>F: webhook, then S2
```

## Correlation

One `correlation_id` follows a flow: it is the `message_id` of the device
event, travels in the envelope, the outbox row (`X-Correlation-ID`), the
work order (`work_orders.correlation_id`) and the logs of every service:

```powershell
docker compose logs --no-log-prefix | Select-String "<correlation_id>"
```

## Technology

Python 3.12, FastAPI, psycopg 3 with explicit SQL, Pydantic v2, paho-mqtt
2.1, Mosquitto 2.0.18, PostgreSQL 16, Keycloak 26, uv workspace (ADR-001).
Code is synchronous; services scale by replicas.

# Integration patterns

The requirement is at least one pattern. The main ones are the
Content-Based Router and the Aggregator; the others follow from what the
project needs.

| Pattern | Source | Where | Why |
|---|---|---|---|
| File Transfer | EIP ch. 2 | [`tools/importer/`](../tools/importer/src/vigiles_importer/pipeline.py) | grade 3.0: contractor files into the registry |
| Remote Procedure Invocation | EIP ch. 2 | [`registry/api.py`](../services/registry/src/vigiles_registry/api.py), [`field_app/sync/`](../services/field_app/src/vigiles_field_app/sync/engine.py) | grade 4.0: two-way sync over REST |
| Messaging | EIP ch. 2 | [`hub/`](../services/hub/src/vigiles_hub/app.py), [`gateway/`](../services/gateway/src/vigiles_gateway/buffer.py) | grade 5.0: devices, hub, MQTT |
| Content-Based Router | EIP | [`hub/ingress/router.py`](../services/hub/src/vigiles_hub/ingress/router.py) | canonical message to `alarm`, `fault` or `telemetry` by `type`; declarative table; unknown type -> dead letter |
| Aggregator | EIP | [`hub/consumers/aggregator.py`](../services/hub/src/vigiles_hub/consumers/aggregator.py) | alarm coincidence: key (site, zone), complete with 2 different detectors, timeout of the window |
| Normalizer (Message Translator per vendor) | EIP | [`hub/ingress/translators/`](../services/hub/src/vigiles_hub/ingress/translators/__init__.py) | two vendor formats -> canonical model |
| Message Translator | EIP | [`field_app/sync/translator.py`](../services/field_app/src/vigiles_field_app/sync/translator.py) | registry model <-> field app model |
| Canonical Data Model | EIP | [`libs/common/envelope.py`](../libs/common/src/vigiles_common/envelope.py) | one envelope for every consumer |
| Content Enricher | EIP | [`hub/ingress/enricher.py`](../services/hub/src/vigiles_hub/ingress/enricher.py) | site, zone, asset from the registry device catalogue; TTL cache, stale cache on outage |
| Message Filter | EIP | [`hub/consumers/telemetry.py`](../services/hub/src/vigiles_hub/consumers/telemetry.py), [`rules.py`](../services/hub/src/vigiles_hub/consumers/rules.py) | only readings above the threshold become work orders |
| Competing Consumers | EIP | `$share/...` in [`hub/app.py`](../services/hub/src/vigiles_hub/app.py) | two hub replicas share the load |
| Idempotent Receiver | EIP | `processed_messages`, `applied_changes`, `idempotency_keys` ([`consumers/base.py`](../services/hub/src/vigiles_hub/consumers/base.py), [`sync/push.py`](../services/registry/src/vigiles_registry/sync/push.py), [`idempotency.py`](../services/registry/src/vigiles_registry/idempotency.py)) | at-least-once without double effects |
| Dead Letter Channel | EIP | `dead_letters`, `vigiles/dlq/#`, [`hub/dlq.py`](../services/hub/src/vigiles_hub/dlq.py), replay in [`console.py`](../services/hub/src/vigiles_hub/console.py) | invalid and undeliverable messages, with replay |
| Wire Tap | EIP | [`hub/events.py`](../services/hub/src/vigiles_hub/events.py), console SSE, `integration_events` | watching the flow without affecting it |
| Correlation Identifier | EIP | [`libs/common/correlation.py`](../libs/common/src/vigiles_common/correlation.py) | one path through all services |
| Guaranteed Delivery | EIP | QoS 1, durable sessions ([`libs/common/mqtt.py`](../libs/common/src/vigiles_common/mqtt.py)), gateway buffer | no loss when a receiver fails |
| Channel Adapter | EIP | [`gateway/`](../services/gateway/src/vigiles_gateway/app.py) | devices -> MQTT |
| Polling Consumer | EIP | importer `watch`, change feed pull | files and feed are read by polling |
| Transactional Outbox | microservices.io (C. Richardson) | [`libs/common/outbox.py`](../libs/common/src/vigiles_common/outbox.py), outbox tables of registry and hub, `pending_changes` of the field app | no dual write (database + network) |
| Retry with backoff and jitter | Cloud Design Patterns (Microsoft) | [`libs/common/retry.py`](../libs/common/src/vigiles_common/retry.py) | retries while a system is unavailable |

## Aggregator rules

- one open window per zone (partial unique index on `alarm_windows`); a
  concurrent insert by the other replica raises `unique_violation` and the
  transaction is retried,
- an event belongs to a window when `|occurred_at - opened_at| <= W`,
  `W` = 60 s (configurable, demonstration value),
- 2 different detectors -> `confirmed` and an outbox row `POST /incidents`
  with `kind = alarm_potwierdzony`,
- the sweeper of each replica (every 5 s) expires open windows past
  `closes_at + L` (`L` = 10 s lateness) into `alarm_wstepny`; rows are
  claimed with `FOR UPDATE SKIP LOCKED`,
- the incident `Idempotency-Key` is the window id plus the outcome,
- windows use event time (`occurred_at`), not arrival time.

Thresholds (configurable, demonstration values, not from a standard):
damper closing time > 60 s -> fault; detector contamination >= 80 % ->
cleaning.

Tests: `tests/unit/test_router.py`, `tests/unit/test_aggregator.py`,
`tests/unit/test_translators.py`, `tests/component/test_hub.py`,
`tests/e2e/test_messaging.py`.

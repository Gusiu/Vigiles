# Resilience

## Buffering on three levels

1. **Gateway.** Every device message is written to SQLite first and
   deleted only after the broker's PUBACK. While the broker is
   unreachable the buffer grows; the client reconnects with backoff
   1-30 s and sends the backlog in order. A message without PUBACK for
   10 s is sent again.
2. **Broker.** Hub consumers use durable MQTT 5 sessions
   (`clean_start=False`, Session Expiry Interval 24 h) and QoS 1. While a
   consumer is away the broker queues its messages.
3. **Services.** Every side effect leaving a service goes through a
   transactional outbox written in the same transaction as the state
   change: hub -> registry, registry -> field app (webhook), field app ->
   registry (`pending_changes`). Relays lease rows with
   `FOR UPDATE SKIP LOCKED` and a lease time, send outside the
   transaction and record the result, so several instances never send the
   same row and a crashed instance releases its rows.

The hub ingress keeps no state: it publishes the canonical message, waits
for PUBACK and only then acknowledges the raw message. Consumers
acknowledge after their transaction committed (one worker per connection,
acks in arrival order, Receive Maximum = queue size). A failing handler
is retried and its message is not acknowledged, so the broker keeps it.

## Retry policy

`libs/common/retry.py`, shared by all relays:

- delay `random(0, min(cap, base * 2**attempt))` (full jitter), base 1 s,
  cap 30 s; a longer `Retry-After` wins,
- retried: connection errors, timeouts, 5xx, 429, 401 (once, after a new
  token),
- not retried: 400, 403, 404, 409, 422 -> dead immediately,
- after 15 attempts `dead`; the hub records it in the dead letter table
  for manual replay.

## Failure matrix

Each row is a test in `tests/chaos/`, run on the local stack (all services
as separate processes, `NodeController(local)`), locally and in CI.
`NodeController` also has a `compose` backend (`docker compose
stop/kill/start`), but the test harness starts only the local stack; on
Docker the scenarios are run by hand from the runbook, and CI checks the
compose stack with a smoke test (S1, S4, Keycloak tokens). Outages
are shortened from the 60/30 s of the plan to 5 s (`CHAOS_OUTAGE_S`) -
what matters is that work continues during the outage and everything
arrives exactly once afterwards.

| # | Node | Action | Expected | Mechanism | Test | Result |
|---|---|---|---|---|---|---|
| C1 | registry | stop; meanwhile faults from devices and 3 inspections | hub outbox and `pending_changes` grow; after the start everything arrives, no duplicates | outbox, retry, `Idempotency-Key`, `applied_changes` | `test_infrastructure_failures.py::test_c1_registry_down_with_device_faults_and_inspections`, `test_sync_outages.py::test_c1_registry_down_while_technician_works` | passed (11.0 s, 11.7 s) |
| C2 | hub-1 | SIGKILL during a burst of 200 messages | hub-2 handles new messages; messages in flight at hub-1 arrive after its restart; 220 effects, none twice | `$share`, durable session, ack after commit, Idempotent Receiver | `test_messaging_failures.py::test_c2_hub_replica_killed_during_burst` | passed (4.2 s) |
| C3 | mosquitto | stop | gateway buffers and sends the backlog after the start; hub processes everything | store-and-forward, reconnect | `test_messaging_failures.py::test_c3_broker_down_gateway_buffers` | passed (10.7 s) |
| C4 | field-app | stop; manager creates 3 work orders | webhooks wait in the registry outbox; after the start the pull from the cursor gives 3 tasks | change feed cursor, webhook retry | `test_sync_outages.py::test_c4_field_app_down_while_orders_are_created` | passed (4.1 s) |
| C5 | postgres | stop under device traffic | hub does not acknowledge, broker keeps the messages; registry returns 503 with `Retry-After`; field app keeps working on SQLite; after the start everything is processed | ack after commit, readiness, separate database of A | `test_infrastructure_failures.py::test_c5_postgres_down` | passed (11.3 s) |
| C6 | keycloak (dev issuer locally) | stop | calls with valid tokens keep working (cached JWKS and tokens); UI login unavailable; normal after the start | token and key caches | `test_infrastructure_failures.py::test_c6_identity_provider_down` | passed (3.2 s) |
| C7 | gateway | SIGKILL | broker publishes the Last Will `offline`; hub creates `brak_lacznosci` work orders | MQTT Last Will | `test_messaging_failures.py::test_c7_gateway_killed_last_will` | passed (20.4 s, includes the 20 s grace period) |

Run of 2026-11-04 (local mode, Ubuntu 24.04, PostgreSQL 16.15, Mosquitto
2.0.18): `uv run pytest -m chaos -v` - 8 passed in 153 s.

## Known limitations

These are stated rather than hidden; each was observed in tests.

- **PostgreSQL is a single instance.** Its outage stops processing without
  losing data (C5): the hub does not acknowledge, the broker keeps the
  messages, the field app works offline. Replication is out of scope.
- **Mosquitto saves its state periodically and at shutdown.** The
  configuration writes it every second (`autosave_interval 1`). Measured
  with 2.0.18: messages acknowledged with PUBACK and killed with SIGKILL
  immediately afterwards were lost (0 of 5 after the restart); killed 2 s
  later, all 5 survived. A hard kill of the broker can therefore lose up
  to about one second of accepted messages. A graceful stop loses
  nothing (C3).
- **`$share` subscriptions are not restored after a broker restart.**
  Mosquitto 2.0.18 restores a durable session and its plain
  subscriptions, but not its shared subscriptions
  (`tests/component/test_broker_persistence.py`). Until the hub consumers
  subscribe again, messages for them have no receiver and are dropped.
  The consumers subscribe on every connect, and the gateway holds its
  backlog for 3 s after reconnecting (`GATEWAY_RECONNECT_HOLD_S`) so they
  can; without the hold C3 lost messages in 1 of 4 observed runs. The
  remaining window is a device message published by another client in
  the first moments after a broker restart.
- **A broker restart also releases the gateway's Last Will.** A delayed
  will (MQTT 5 Will Delay Interval) does not help: the gateway
  reconnects with a clean start, which ends its old session and releases
  the will at once. The hub therefore holds connectivity work orders for
  20 s (`GATEWAY_OFFLINE_GRACE_S`) and cancels them when the same gateway
  session reports `online` again; a gateway that really died returns
  with a new session. If the `online` status is processed before the
  `offline` fault reaches the outbox (different replicas), an order can
  still be created.
- **`$share` with QoS 1 does not keep global order.** Aggregation uses
  event time; a very late alarm (older than an open window) is logged
  and not aggregated.
- **Which `$share` variant Mosquitto implements.** The MQTT 5
  specification allows the broker to wait for a disconnected member of a
  shared group or to hand its messages to another member. Mosquitto
  2.0.18 waits: it keeps distributing round-robin to a disconnected
  member with a durable session and queues the messages for it (measured:
  5 of 10 messages went to the offline member and arrived after it
  reconnected). In C2 this is why part of the burst is processed only
  after hub-1 restarts. The test passes in either variant because it
  counts effects.
- **Beta sends no message id.** Its `message_id` is derived from device,
  time to the second, type and code; two identical events of one device
  in the same second are treated as one.
- **Keycloak runs in dev mode** (embedded database, single node). While
  it is down, UI login is unavailable.
- **A sync change rejected by the registry** stays rejected in the field
  app, shown in the sync status; its local value is not rolled back until
  the next server change of that entity.

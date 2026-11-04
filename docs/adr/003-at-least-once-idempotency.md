# ADR-003: at-least-once delivery with idempotency instead of exactly-once

Status: accepted

## Context

Messages cross the broker, the hub, its outbox, the registry, the
webhook and the field app outbox. Any of them can fail between two steps.
Exactly-once delivery across MQTT, HTTP and two databases would need
distributed transactions that none of these components offer.

## Decision

Every hop delivers at least once and every receiver is idempotent, which
gives "exactly once" in effect:

- deterministic `message_id` (UUIDv5) of device events and
  `processed_messages` per hub consumer,
- outbox rows with stable keys and `Idempotency-Key` towards the registry
  (`idempotency_keys`),
- `change_id` of field app changes and `applied_changes` in the registry,
- every side effect leaving a service goes through a transactional outbox.

## Consequences

- Duplicates are normal and visible as `duplicate_dropped` events.
- Beta devices send no message id; identical events of one device in the
  same second share a `message_id` and are treated as one (accepted).
- Tests count effects, not deliveries (C1, C2).

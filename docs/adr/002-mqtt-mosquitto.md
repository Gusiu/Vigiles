# ADR-002: MQTT and Mosquitto instead of AMQP and RabbitMQ

Status: accepted

## Context

Device messages come from an IoT gateway in a building: small payloads,
unreliable links, devices that disappear. The course literature names
MQTT; grade 5.0 needs buffering, retries and at least three components.

## Decision

MQTT 5 (OASIS standard) with Mosquitto 2. QoS 1, durable sessions
(`clean_start=False`, Session Expiry Interval > 0), shared subscriptions
(`$share`) for competing hub replicas, retained status and Last Will for
gateway liveness, ACL per user.

## Consequences

- Last Will gives gateway liveness without polling.
- MQTT has no broker-side dead letter queue or per-message TTL routing;
  the hub implements the Dead Letter Channel in PostgreSQL.
- `$share` with QoS 1 does not keep global order, hence aggregation by
  event time.
- Observed in Mosquitto 2.0.18 and documented in
  [resilience.md](../resilience.md): after a broker restart `$share`
  subscriptions are not restored with the session, and a Last Will is
  published when the broker restarts under a healthy gateway. Both are
  mitigated in the gateway and the hub.
- RabbitMQ would offer DLX and richer routing but is not the standard
  named for the course and adds an AMQP client to the gateway.

# ADR-006: aggregator state in PostgreSQL as a consequence of Competing Consumers

Status: accepted

## Context

Two hub replicas consume `$share/aggregator/...`. Two alarms from one
zone can reach different replicas, in any order. An in-memory window per
replica would never see both detectors.

## Decision

Alarm windows live in the shared hub database. One open window per zone
is enforced by a partial unique index; each event locks the window row;
a concurrent creation raises `unique_violation` and is retried. The
sweeper of every replica expires windows with `FOR UPDATE SKIP LOCKED`.

## Consequences

- Correct coincidence with any number of replicas.
- Each alarm costs a database transaction; fine for alarm rates of a
  building.
- PostgreSQL is a single point of failure for the hub: while it is down
  the hub does not acknowledge messages and the broker keeps them (C5).

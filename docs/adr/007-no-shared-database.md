# ADR-007: no shared database between System A and System B

Status: accepted

## Context

Shared Database is one of the four integration styles, and the simplest
way to keep the registry and the field app in sync.

## Decision

The registry (PostgreSQL) and the field app (SQLite) have separate
databases and deliberately different models. They integrate only through
the REST contract (change feed, push, webhook).

## Consequences

- The field app keeps working offline and while the registry is down.
- Each system can change its schema independently; the translator is the
  only place that knows both models.
- The project needs a sync protocol with conflict handling (grade 4.0),
  which is the point of the exercise.
- The importer does write directly into the registry database: it is a
  tool of System B, not another system.

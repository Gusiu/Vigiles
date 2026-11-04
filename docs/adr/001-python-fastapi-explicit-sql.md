# ADR-001: Python, FastAPI and explicit SQL

Status: accepted

## Context

Five services and a CLI must be built by one person within a course, with
tests on every level. The data model is small but the integration logic
(merge, outbox, idempotency, window locking) depends on precise SQL
semantics: `FOR UPDATE`, `SKIP LOCKED`, partial unique indexes,
`IS DISTINCT FROM`, triggers.

## Decision

Python 3.12, FastAPI, Pydantic v2 and psycopg 3 with hand-written SQL, no
ORM. One uv workspace with a shared library. Synchronous code; the only
async endpoint is the SSE stream. Scaling by replicas.

## Consequences

- The SQL that decides correctness is visible and reviewed as is.
- No ORM session or identity map hides when a transaction commits, which
  matters for "commit, then acknowledge".
- Migrations are numbered SQL files with a small runner and checksums.
- Throughput per process is lower than with asyncio; the hub scales out
  with a second replica instead.

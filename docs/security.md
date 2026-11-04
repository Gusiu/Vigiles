# Security

## Identity: Keycloak, OAuth2 / OIDC

Realm `vigiles`, imported from `infra/keycloak/realm.json`, which
`scripts/gen_secrets.py` renders from `realm.template.json` with the
secrets from `.env` (ADR-005).

| Client | Type | Flow | Used for |
|---|---|---|---|
| `registry-ui`, `field-app-ui`, `hub-console` | confidential | Authorization Code + PKCE (S256) | login of people (Authlib) |
| `svc-hub` | service account | client credentials | hub -> registry |
| `svc-field-app` | service account | client credentials | field app -> registry |
| `svc-registry` | service account | client credentials | registry -> field app (webhook) |

Each API is its own audience: `registry-api`, `field-app-api`, `hub-api`,
added by Audience mappers (without them Keycloak issues `aud: account`
and validation rejects the token).

Demo users: `jan.technik`, `anna.kierownik`, `zarzadca.b1`, `admin`, with
the password `DEMO_USER_PASSWORD` from `.env`.

## Authentication modes (`AUTH_MODE`)

One interface (`TokenVerifier`, `TokenProvider`); switching the mode needs
no change in the services.

| Mode | Services | UI | Where |
|---|---|---|---|
| `static` | bearer tokens from `SERVICE_TOKENS` | demo user picker, marked "demo mode" | grade 4.0 |
| `dev` | JWTs from `tools/dev_issuer`, same structure as Keycloak | demo user picker | local mode and tests, never in compose |
| `oidc` | JWTs from Keycloak | login at Keycloak | compose |

## Token validation

`libs/common/src/vigiles_common/auth/jwt.py`:

- only RS256; `none` and HS* are rejected,
- `iss`, `aud`, `exp`, `nbf` with 30 s leeway,
- keys from JWKS through `PyJWKClient` with a one-hour cache,
- `OIDC_ISSUER` (compared with `iss`) and `OIDC_JWKS_URL` (internal
  address) are separate: in Docker the browser sees Keycloak as
  `localhost:8080`, the services as `keycloak:8080`. Keycloak runs with
  the fixed hostname `http://localhost:8080`, so tokens fetched by
  services over the internal address carry the same issuer.

Service tokens (client credentials) are cached until 30 s before expiry
and fetched again after a 401.

## Roles and permissions

Mapping in code (`auth/core.py`), enforced by the FastAPI dependency
`require("...")`; roles come from `realm_access.roles`.

| Role | Permissions |
|---|---|
| `technik` | `tasks:read`, `inspections:write` |
| `kierownik` | `assets:read`, `assets:write`, `work-orders:create`, `work-orders:assign`, `conflicts:read`, `conflicts:resolve`, `incidents:read`, `imports:read`, `console:read` |
| `zarzadca` | `assets:read`, `incidents:read` |
| `admin` | all of the above + `dlq:read`, `dlq:replay` |
| `svc-hub` | `devices:read`, `work-orders:create`, `incidents:create` |
| `svc-field-app` | `sync:read`, `sync:push` |
| `svc-registry` | `webhook:notify` |

`assets:write` (editing notes in the registry UI) is an addition to the
plan's table, needed for scenario S3.

Missing or invalid token -> `401` with `WWW-Authenticate: Bearer`;
missing permission -> `403`; both `application/problem+json`. The full
role x endpoint matrix is tested in `tests/component/test_rbac_matrix.py`,
with tokens for another API's audience rejected as `401`.

## Broker

Mosquitto with `allow_anonymous false`; the password file is created at
container start from `.env`; ACL per user (`infra/mosquitto/acl`):

| User | May |
|---|---|
| `gw-01` | publish `vigiles/raw/+/gw-01/#` and `vigiles/status/gw-01` only |
| `hub` | read `vigiles/#`, publish `vigiles/events/#` and `vigiles/dlq/#` |
| `explorer` | read `vigiles/#` (MQTT Explorer, demo) |

Tested in `tests/component/test_broker_acl.py`.

## Databases

Separate roles with minimal privileges (`infra/postgres/init.sql`):
`registry_app` owns the registry database, `hub_app` the hub database,
`importer` may only select, insert and update the tables it loads (no
delete, no DDL). History and change feed are written by a
`SECURITY DEFINER` trigger, so the importer needs no access to them.
Tested in `tests/component/test_registry_schema.py`.

## Secrets

Only in `.env` (in `.gitignore`); the repository holds `.env.example`
and `realm.template.json`. `python scripts/gen_secrets.py` creates `.env`
with random values and renders the realm. The gateway scenario API
listens on localhost only and requires `X-Api-Key`.

## Out of scope

TLS inside the compose network, a production Keycloak (database,
cluster), rotating secrets.

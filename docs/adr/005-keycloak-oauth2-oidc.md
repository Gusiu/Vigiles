# ADR-005: Keycloak and OAuth2/OIDC; OAuth2 rather than SAML for the APIs

Status: accepted

## Context

Grade 5.0 requires authorisation based on a standard (SAML tokens, OAuth2
or role-based access control). There are human users of three UIs and
three service-to-service flows.

## Decision

Keycloak as the external identity provider. Services use the OAuth2
client credentials grant, UIs the authorization code flow with PKCE.
Access tokens are JWTs validated in each service (RS256, issuer, audience
per API, expiry); roles from `realm_access.roles` map to permissions in
code and are enforced by `require("...")`. No own OAuth2 server; the dev
issuer exists only for local mode and tests.

SAML is designed for browser single sign-on with XML assertions passed
through the browser. For REST APIs and service accounts it has no
equivalent of the client credentials grant, its assertions are large XML
documents for every call, and the Python libraries are much heavier.
OAuth2 bearer tokens are the standard for API access; OIDC adds the login
of the UIs on the same server.

## Consequences

- Inside Docker the browser and the services reach Keycloak under
  different host names; issuer and JWKS URL are configured separately.
- Audience mappers are required, otherwise tokens carry `aud: account`.
- Cached keys and tokens keep service calls working while Keycloak is
  down (C6); UI login is unavailable then.

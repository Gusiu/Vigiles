"""Selection of token verifier and provider from ``AUTH_MODE``.

* ``static`` - SERVICE_TOKENS, for grade 4.0,
* ``dev``    - JWTs from tools/dev_issuer (local mode and tests),
* ``oidc``   - JWTs from Keycloak.

dev and oidc are the same code path; only the addresses differ.
"""

import os

from vigiles_common.auth.core import StaticTokenProvider, StaticTokenVerifier, TokenProvider, TokenVerifier, parse_service_tokens
from vigiles_common.auth.jwt import ClientCredentialsProvider, JwtVerifier


def build_verifier(mode: str, service_tokens: str = "", audience: str | None = None) -> TokenVerifier:
    if mode == "static":
        tokens = parse_service_tokens(service_tokens)
        if not tokens:
            raise ValueError("AUTH_MODE=static requires SERVICE_TOKENS")
        return StaticTokenVerifier(tokens)
    if mode in ("dev", "oidc"):
        if not audience:
            raise ValueError("JWT validation needs the audience of this API")
        return JwtVerifier(os.environ["OIDC_ISSUER"], os.environ["OIDC_JWKS_URL"], audience)
    raise ValueError(f"unknown AUTH_MODE {mode!r}")


def build_token_provider(mode: str, client_id: str, service_tokens: str = "") -> TokenProvider:
    """Credentials of this service when it calls another one."""
    if mode == "static":
        return StaticTokenProvider(parse_service_tokens(service_tokens)[client_id])
    if mode in ("dev", "oidc"):
        secret = os.environ[f"OIDC_SECRET_{client_id.upper().replace('-', '_')}"]
        return ClientCredentialsProvider(os.environ["OIDC_TOKEN_URL"], client_id, secret)
    raise ValueError(f"unknown AUTH_MODE {mode!r}")

"""Selection of token verifier and provider from ``AUTH_MODE``.

* ``static`` - SERVICE_TOKENS, for grade 4.0,
* ``dev``    - JWTs from tools/dev_issuer (local mode and tests),
* ``oidc``   - JWTs from Keycloak.

dev and oidc are the same code path; only the addresses differ.
"""

from vigiles_common.auth.core import (
    StaticTokenProvider,
    StaticTokenVerifier,
    TokenProvider,
    TokenVerifier,
    parse_service_tokens,
)
from vigiles_common.auth.jwt import ClientCredentialsProvider, JwtVerifier
from vigiles_common.config import AuthSettings


def build_verifier(settings: AuthSettings, audience: str) -> TokenVerifier:
    if settings.auth_mode == "static":
        tokens = parse_service_tokens(settings.service_tokens)
        if not tokens:
            raise ValueError("AUTH_MODE=static requires SERVICE_TOKENS")
        return StaticTokenVerifier(tokens)
    if not settings.oidc_issuer or not settings.oidc_jwks_url:
        raise ValueError(f"AUTH_MODE={settings.auth_mode} requires OIDC_ISSUER and OIDC_JWKS_URL")
    return JwtVerifier(settings.oidc_issuer, settings.oidc_jwks_url, audience)


def build_token_provider(settings: AuthSettings, client_id: str) -> TokenProvider:
    """Credentials of this service when it calls another one."""
    if settings.auth_mode == "static":
        return StaticTokenProvider(parse_service_tokens(settings.service_tokens)[client_id])
    secret = getattr(settings, f"oidc_secret_{client_id.replace('-', '_')}")
    if not secret or not settings.oidc_token_url:
        raise ValueError(f"AUTH_MODE={settings.auth_mode} requires OIDC_TOKEN_URL and the secret of {client_id}")
    return ClientCredentialsProvider(settings.oidc_token_url, client_id, secret)

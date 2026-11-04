"""JWT validation and client-credentials tokens (AUTH_MODE=dev and oidc).

Validation: RS256 only (``none`` and HS* are rejected), ``iss``, ``aud``,
``exp`` and ``nbf`` checked with 30 s leeway, keys from JWKS through
``PyJWKClient`` with a cache. The issuer compared with ``iss`` and the
JWKS address are configured separately: inside Docker the browser sees
Keycloak as localhost:8080, the services as keycloak:8080 (PLAN.md
pitfall 10). Cached keys and tokens keep service calls working while the
identity provider is down (failure C6).
"""

import threading
import time
from collections.abc import Callable

import httpx
import jwt

from vigiles_common.auth.core import AuthError, Principal

LEEWAY_S = 30


class JwtVerifier:
    def __init__(self, issuer: str, jwks_url: str, audience: str, leeway_s: int = LEEWAY_S,
                 jwks_client: jwt.PyJWKClient | None = None) -> None:
        self.issuer = issuer
        self.audience = audience
        self.leeway_s = leeway_s
        # lifespan: keys stay cached for an hour, so a restart of the identity
        # provider does not interrupt validation of tokens already issued.
        self.jwks = jwks_client or jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=3600, timeout=5)

    def verify(self, token: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthError(f"malformed token: {exc}") from exc
        if header.get("alg") != "RS256":
            raise AuthError(f"algorithm {header.get('alg')!r} is not accepted")
        try:
            key = self.jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token, key, algorithms=["RS256"], audience=self.audience, issuer=self.issuer,
                leeway=self.leeway_s, options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.PyJWKClientError as exc:
            raise AuthError(f"signing key not available: {exc}") from exc
        except jwt.PyJWTError as exc:
            raise AuthError(str(exc)) from exc
        roles = tuple(claims.get("realm_access", {}).get("roles", []))
        service = claims.get("preferred_username", "").startswith("service-account-")
        subject = claims.get("azp") if service else claims.get("preferred_username") or claims.get("sub")
        return Principal.of(subject, roles, "service" if service else "user")


class ClientCredentialsProvider:
    """OAuth2 client credentials with the token cached until shortly
    before it expires; ``invalidate()`` forces a new one after a 401."""

    def __init__(self, token_url: str, client_id: str, client_secret: str, http: httpx.Client | None = None,
                 margin_s: float = 30.0, clock: Callable[[], float] = time.time) -> None:
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.http = http or httpx.Client(timeout=5)
        self.margin_s = margin_s
        self.clock = clock
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def token(self) -> str:
        with self._lock:
            if self._token and self.clock() < self._expires_at - self.margin_s:
                return self._token
            response = self.http.post(self.token_url, data={"grant_type": "client_credentials"},
                                      auth=(self.client_id, self.client_secret))
            response.raise_for_status()
            body = response.json()
            self._token = body["access_token"]
            self._expires_at = self.clock() + float(body.get("expires_in", 300))
            return self._token

    def invalidate(self) -> None:
        with self._lock:
            self._token = None

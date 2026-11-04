import time

import httpx
import jwt
import pytest

from vigiles_common.auth.core import AuthError
from vigiles_common.auth.jwt import ClientCredentialsProvider, JwtVerifier
from vigiles_dev_issuer import Issuer

ISS = "http://localhost:8080/realms/vigiles"


class StaticJwks:
    """PyJWKClient stand-in serving the issuer's keys without HTTP."""

    def __init__(self, issuer: Issuer) -> None:
        self.keys = jwt.PyJWKSet.from_dict(issuer.jwks())

    def get_signing_key_from_jwt(self, token: str):
        kid = jwt.get_unverified_header(token).get("kid")
        for key in self.keys.keys:
            if key.key_id == kid:
                return key
        raise jwt.PyJWKClientError("unknown kid")


@pytest.fixture(scope="module")
def issuer() -> Issuer:
    return Issuer.load_or_create(ISS)


@pytest.fixture
def verifier(issuer: Issuer) -> JwtVerifier:
    return JwtVerifier(ISS, "unused", "registry-api", jwks_client=StaticJwks(issuer))


def test_service_token_is_accepted(issuer: Issuer, verifier: JwtVerifier) -> None:
    principal = verifier.verify(issuer.service_token("svc-hub"))

    assert (principal.subject, principal.kind, principal.roles) == ("svc-hub", "service", ("svc-hub",))
    assert principal.can("work-orders:create")


def test_user_token_roles(issuer: Issuer, verifier: JwtVerifier) -> None:
    principal = verifier.verify(issuer.user_token("jan.technik", ["technik", "offline_access"], "registry-api"))

    assert (principal.subject, principal.kind) == ("jan.technik", "user")
    assert principal.can("inspections:write") and not principal.can("conflicts:resolve")


@pytest.mark.parametrize(
    ("claims", "message"),
    [
        ({"audience": "account"}, "(?i)audience"),
        ({"audience": "field-app-api"}, "(?i)audience"),
        ({"lifetime_s": -60}, "expired"),
        ({"now": time.time() + 120}, "not yet valid"),
    ],
)
def test_invalid_claims(issuer: Issuer, verifier: JwtVerifier, claims: dict, message: str) -> None:
    args = {"subject": "svc-hub", "roles": ["svc-hub"], "audience": "registry-api", "azp": "svc-hub", "service": True}
    args.update(claims)
    with pytest.raises(AuthError, match=message):
        verifier.verify(issuer.issue(**args))


def test_leeway_tolerates_small_clock_skew(issuer: Issuer, verifier: JwtVerifier) -> None:
    token = issuer.issue(subject="svc-hub", roles=["svc-hub"], audience="registry-api", azp="svc-hub", service=True,
                         lifetime_s=-10)
    assert verifier.verify(token).subject == "svc-hub"


def test_wrong_issuer(issuer: Issuer) -> None:
    other = JwtVerifier("http://keycloak:8080/realms/vigiles", "unused", "registry-api", jwks_client=StaticJwks(issuer))
    with pytest.raises(AuthError, match="issuer"):
        other.verify(issuer.service_token("svc-hub"))


def test_foreign_key_is_rejected(verifier: JwtVerifier) -> None:
    stranger = Issuer.load_or_create(ISS)
    stranger.kid = "dev-1"
    with pytest.raises(AuthError):
        verifier.verify(stranger.service_token("svc-hub"))


@pytest.mark.parametrize("alg", ["HS256", "none"])
def test_symmetric_and_unsigned_tokens_are_rejected(verifier: JwtVerifier, alg: str) -> None:
    claims = {"iss": ISS, "aud": "registry-api", "exp": int(time.time()) + 60, "realm_access": {"roles": ["admin"]}}
    token = jwt.encode(claims, "a-shared-secret-of-thirty-two-bytes!" if alg == "HS256" else None, algorithm=alg)
    with pytest.raises(AuthError, match="not accepted"):
        verifier.verify(token)


def test_client_credentials_token_is_cached_until_expiry() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"access_token": f"t{len(calls)}", "expires_in": 300})

    now = [1000.0]
    provider = ClientCredentialsProvider("http://idp/token", "svc-hub", "s3cret",
                                         http=httpx.Client(transport=httpx.MockTransport(handler)), clock=lambda: now[0])

    assert provider.token() == "t1"
    assert provider.token() == "t1"
    now[0] += 280  # within the 30 s refresh margin
    assert provider.token() == "t2"
    provider.invalidate()
    assert provider.token() == "t3"
    assert calls[0].headers["authorization"].startswith("Basic ")
    assert b"grant_type=client_credentials" in calls[0].content

"""JWTs for tests, signed by an in-process dev issuer."""

import jwt

from vigiles_common.auth.jwt import JwtVerifier
from vigiles_dev_issuer import Issuer

ISSUER = "http://127.0.0.1:18006"
_issuer = Issuer.load_or_create(ISSUER)


class _Jwks:
    def __init__(self) -> None:
        self.keys = jwt.PyJWKSet.from_dict(_issuer.jwks())

    def get_signing_key_from_jwt(self, token: str):
        return self.keys.keys[0]


def verifier(audience: str) -> JwtVerifier:
    return JwtVerifier(ISSUER, "unused", audience, jwks_client=_Jwks())


def user(name: str, roles: list[str], audience: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_issuer.user_token(name, roles, audience)}"}


def service(client_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_issuer.service_token(client_id)}"}

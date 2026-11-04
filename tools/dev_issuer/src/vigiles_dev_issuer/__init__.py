"""Development token issuer.

Signs RS256 tokens shaped like Keycloak access tokens (``iss``, ``aud``,
``azp``, ``realm_access.roles``, ``preferred_username``) so the services
run the same validation code as with Keycloak. For local mode and tests
only: it is not part of compose and implements no user login.
"""

import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# Service clients: id -> (realm role, audience), mirroring the Keycloak realm.
SERVICE_CLIENTS = {
    "svc-hub": ("svc-hub", "registry-api"),
    "svc-field-app": ("svc-field-app", "registry-api"),
    "svc-registry": ("svc-registry", "field-app-api"),
}


@dataclass
class Issuer:
    issuer: str
    key: rsa.RSAPrivateKey
    kid: str = "dev-1"

    @classmethod
    def load_or_create(cls, issuer: str, key_path: Path | None = None) -> "Issuer":
        if key_path and key_path.exists():
            key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        else:
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            if key_path:
                key_path.parent.mkdir(parents=True, exist_ok=True)
                key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                       serialization.NoEncryption()))
                key_path.chmod(0o600)
        return cls(issuer, key)

    def jwks(self) -> dict:
        public = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        return {"keys": [{**public, "kid": self.kid, "use": "sig", "alg": "RS256"}]}

    def issue(self, *, subject: str, roles: list[str], audience: str | list[str], azp: str,
              service: bool, lifetime_s: int = 300, now: float | None = None, **extra) -> str:
        now = now if now is not None else time.time()
        claims = {
            "iss": self.issuer, "aud": audience, "sub": str(uuid.uuid5(uuid.NAMESPACE_URL, subject)),
            "azp": azp, "iat": int(now), "nbf": int(now), "exp": int(now + lifetime_s), "typ": "Bearer",
            "jti": str(uuid.uuid4()), "realm_access": {"roles": roles},
            "preferred_username": f"service-account-{subject}" if service else subject, **extra,
        }
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": self.kid})

    def service_token(self, client_id: str, lifetime_s: int = 300) -> str:
        role, audience = SERVICE_CLIENTS[client_id]
        return self.issue(subject=client_id, roles=[role], audience=audience, azp=client_id, service=True,
                          lifetime_s=lifetime_s)

    def user_token(self, user: str, roles: list[str], audience: str) -> str:
        return self.issue(subject=user, roles=roles, audience=audience, azp="registry-ui", service=False)

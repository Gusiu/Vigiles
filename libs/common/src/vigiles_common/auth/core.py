"""Principals, the role -> permission mapping and static tokens."""

import hmac
from dataclasses import dataclass, field
from typing import Protocol

_HUMAN = {
    "technik": {"tasks:read", "inspections:write"},
    "kierownik": {
        "assets:read", "work-orders:create", "work-orders:assign", "conflicts:read",
        "conflicts:resolve", "incidents:read", "imports:read", "console:read",
    },
    "zarzadca": {"assets:read", "incidents:read"},
}
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    **{role: frozenset(p) for role, p in _HUMAN.items()},
    "admin": frozenset(set().union(*_HUMAN.values()) | {"dlq:read", "dlq:replay"}),
    "svc-hub": frozenset({"devices:read", "work-orders:create", "incidents:create"}),
    "svc-field-app": frozenset({"sync:read", "sync:push"}),
    "svc-registry": frozenset({"webhook:notify"}),
}

# Demo users of the identity provider (PLAN.md 11.1) and their roles.
DEMO_USERS: dict[str, tuple[str, ...]] = {
    "jan.technik": ("technik",),
    "anna.kierownik": ("kierownik",),
    "zarzadca.b1": ("zarzadca",),
    "admin": ("admin",),
}


def permissions_for(roles: tuple[str, ...] | list[str]) -> frozenset[str]:
    result: set[str] = set()
    for role in roles:
        result |= ROLE_PERMISSIONS.get(role, frozenset())
    return frozenset(result)


@dataclass(frozen=True)
class Principal:
    subject: str  # user name or client id
    roles: tuple[str, ...]
    kind: str = "service"  # "service" or "user"
    permissions: frozenset[str] = field(default=frozenset())

    @classmethod
    def of(cls, subject: str, roles: tuple[str, ...] | list[str], kind: str) -> "Principal":
        return cls(subject, tuple(roles), kind, permissions_for(roles))

    def can(self, permission: str) -> bool:
        return permission in self.permissions


class AuthError(Exception):
    """Missing, malformed, expired or otherwise invalid credentials (401)."""


class TokenVerifier(Protocol):
    def verify(self, token: str) -> Principal: ...


class TokenProvider(Protocol):
    def token(self) -> str: ...

    def invalidate(self) -> None: ...


def parse_service_tokens(value: str) -> dict[str, str]:
    """``"svc-hub=abc,svc-field-app=def"`` -> ``{"svc-hub": "abc", ...}``."""
    tokens: dict[str, str] = {}
    for item in filter(None, (part.strip() for part in value.split(","))):
        name, sep, token = item.partition("=")
        if not sep or not name.strip() or not token.strip():
            raise ValueError(f"invalid SERVICE_TOKENS entry {item!r}, expected name=token")
        tokens[name.strip()] = token.strip()
    return tokens


class StaticTokenVerifier:
    """Static bearer tokens: each service client has one token; the client
    id is also its role (``svc-hub`` -> permissions of ``svc-hub``)."""

    def __init__(self, tokens: dict[str, str]) -> None:
        self._tokens = dict(tokens)

    def verify(self, token: str) -> Principal:
        for client, expected in self._tokens.items():
            if hmac.compare_digest(token.encode(), expected.encode()):
                return Principal.of(client, (client,), "service")
        raise AuthError("unknown token")


class StaticTokenProvider:
    def __init__(self, token: str) -> None:
        self._token = token

    def token(self) -> str:
        return self._token

    def invalidate(self) -> None:
        """Static tokens cannot be refreshed."""

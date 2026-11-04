"""Authentication and role-based access control shared by all services.

Services depend only on the interfaces here; ``AUTH_MODE`` selects the
implementation without code changes in the services:

* ``static`` - service tokens from ``SERVICE_TOKENS``, UI users picked
  from a demo list,
* ``dev`` - JWTs signed by ``tools/dev_issuer`` (local mode and tests),
* ``oidc`` - JWTs issued by Keycloak.
"""

from vigiles_common.auth.core import (
    DEMO_USERS,
    ROLE_PERMISSIONS,
    AuthError,
    Principal,
    StaticTokenVerifier,
    TokenProvider,
    TokenVerifier,
    StaticTokenProvider,
    parse_service_tokens,
    permissions_for,
)
from vigiles_common.auth.fastapi import current_principal, require

__all__ = [
    "DEMO_USERS",
    "ROLE_PERMISSIONS",
    "AuthError",
    "Principal",
    "StaticTokenProvider",
    "StaticTokenVerifier",
    "TokenProvider",
    "TokenVerifier",
    "current_principal",
    "parse_service_tokens",
    "permissions_for",
    "require",
]

"""FastAPI integration: ``Depends(require("sync:push"))``.

The verifier lives in ``app.state.verifier``. Requests authenticate with
``Authorization: Bearer``; browser sessions of the UI may carry a user
in ``request.session["user"]`` (set by the login page of each service).
"""

from collections.abc import Callable

from fastapi import Request

from vigiles_common.auth.core import DEMO_USERS, AuthError, Principal
from vigiles_common.problems import ProblemError

_CHALLENGE = {"WWW-Authenticate": 'Bearer realm="vigiles"'}


def _unauthorized(detail: str) -> ProblemError:
    return ProblemError(401, "Unauthorized", detail, headers=_CHALLENGE)


def current_principal(request: Request) -> Principal:
    cached = getattr(request.state, "principal", None)
    if cached is not None:
        return cached
    header = request.headers.get("authorization")
    if header:
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise _unauthorized("expected 'Authorization: Bearer <token>'")
        try:
            principal = request.app.state.verifier.verify(token.strip())
        except AuthError as exc:
            raise _unauthorized(str(exc)) from exc
    else:
        user = request.scope.get("session", {}).get("user") if "session" in request.scope else None
        if user not in DEMO_USERS:
            raise _unauthorized("authentication required")
        principal = Principal.of(user, DEMO_USERS[user], "user")
    request.state.principal = principal
    return principal


def require(permission: str) -> Callable[[Request], Principal]:
    def dependency(request: Request) -> Principal:
        principal = current_principal(request)
        if not principal.can(permission):
            raise ProblemError(
                403, "Forbidden", f"{principal.subject} lacks permission {permission}"
            )
        return principal

    dependency.__name__ = f"require_{permission.replace(':', '_').replace('-', '_')}"
    return dependency

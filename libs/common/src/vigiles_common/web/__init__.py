"""Server-rendered UI shared by the services: base layout, CSS, htmx and
the demo login used with AUTH_MODE=static and dev.

htmx is vendored from npm (htmx.org 2.0.4, 0BSD licence) - no CDN, the UI
works without internet access.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader
from starlette.middleware.sessions import SessionMiddleware

from vigiles_common.auth.core import DEMO_USERS, AuthError, Principal
from vigiles_common.auth.fastapi import current_principal
from vigiles_common.problems import ProblemError

_HERE = Path(__file__).parent


class LoginRequired(Exception):
    pass


class UiForbidden(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def templates_for(service_templates: Path) -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(service_templates))
    templates.env.loader = ChoiceLoader(
        [FileSystemLoader(str(service_templates)), FileSystemLoader(str(_HERE / "templates"))]
    )
    return templates


def ui_user(request: Request, permission: str | None = None) -> Principal:
    """Principal of a UI request; redirects to the login page when missing."""
    if "user" not in request.session:
        raise LoginRequired()
    try:
        principal = current_principal(request)
    except ProblemError as exc:
        raise LoginRequired() from exc
    if permission and not principal.can(permission):
        raise UiForbidden(f"{principal.subject} lacks permission {permission}")
    return principal


@dataclass(frozen=True)
class OidcLogin:
    """Authorization Code + PKCE against Keycloak for one UI client."""

    client_id: str
    client_secret: str
    public_url: str  # realm URL as the browser sees it
    token_url: str  # realm token endpoint as the service sees it


def oidc_login_for(settings, client_id: str, secret_field: str) -> "OidcLogin | None":
    if getattr(settings, "auth_mode", "static") != "oidc":
        return None
    return OidcLogin(client_id, getattr(settings, secret_field), settings.oidc_public_url.rstrip("/"),
                     settings.oidc_token_url)


def install_web(app: FastAPI, templates: Jinja2Templates, title: str, session_secret: str,
                nav: list[tuple[str, str, str]], home: str, oidc: "OidcLogin | None" = None) -> None:
    """Session, static files, login/logout and HTML error pages.

    ``nav`` lists (label, path, permission) for the menu. With ``oidc`` the
    login page redirects to the identity provider; otherwise it offers the
    demo users (AUTH_MODE=static and dev).
    """
    app.add_middleware(SessionMiddleware, secret_key=session_secret, same_site="lax", https_only=False)
    app.state.demo_login = oidc is None
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")
    templates.env.globals.update(service_title=title, nav=nav, demo_login=oidc is None)

    def render(request: Request, name: str, status_code: int = 200, **context: Any) -> HTMLResponse:
        try:
            principal = current_principal(request) if "user" in request.session else None
        except ProblemError:
            principal = None
        return templates.TemplateResponse(request, name, {"user": principal, **context}, status_code=status_code)

    app.state.render = render

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, _: LoginRequired) -> RedirectResponse:
        return RedirectResponse(f"/login?next={request.url.path}", status_code=303)

    @app.exception_handler(UiForbidden)
    async def _forbidden(request: Request, exc: UiForbidden) -> HTMLResponse:
        return render(request, "forbidden.html", status_code=403, detail=exc.detail)

    @app.get("/", include_in_schema=False, name="index")
    def index() -> RedirectResponse:
        return RedirectResponse(home, status_code=303)

    if oidc is not None:
        _install_oidc(app, oidc, home)
    else:
        _install_demo_login(app, render, home)

    @app.post("/logout", include_in_schema=False)
    def logout(request: Request) -> RedirectResponse:
        request.session.clear()
        if oidc is not None:
            return RedirectResponse(f"{oidc.public_url}/protocol/openid-connect/logout?client_id={oidc.client_id}"
                                    f"&post_logout_redirect_uri={request.url_for('index')}", status_code=303)
        return RedirectResponse("/login", status_code=303)


def _install_demo_login(app: FastAPI, render, home: str) -> None:
    @app.get("/login", include_in_schema=False, response_class=HTMLResponse)
    def login_form(request: Request, next: str = home) -> HTMLResponse:
        return render(request, "login.html", users=DEMO_USERS, next=next)

    @app.post("/login", include_in_schema=False)
    async def login(request: Request) -> RedirectResponse:
        form = await request.form()
        user = str(form.get("user", ""))
        target = str(form.get("next") or home)
        if user not in DEMO_USERS or not target.startswith("/"):
            return RedirectResponse("/login", status_code=303)
        request.session["user"] = user
        return RedirectResponse(target, status_code=303)


def _install_oidc(app: FastAPI, oidc: OidcLogin, home: str) -> None:
    from authlib.integrations.starlette_client import OAuth, OAuthError

    oauth = OAuth()
    oauth.register(
        "idp",
        client_id=oidc.client_id,
        client_secret=oidc.client_secret,
        authorize_url=f"{oidc.public_url}/protocol/openid-connect/auth",
        access_token_url=oidc.token_url,
        client_kwargs={"scope": "profile email", "code_challenge_method": "S256"},
    )

    @app.get("/login", include_in_schema=False)
    async def login(request: Request, next: str = home):
        request.session["next"] = next if next.startswith("/") else home
        return await oauth.idp.authorize_redirect(request, str(request.url_for("auth_callback")))

    @app.get("/auth/callback", include_in_schema=False, name="auth_callback")
    async def callback(request: Request):
        try:
            token = await oauth.idp.authorize_access_token(request)
            # The access token carries the roles; it is validated like any
            # API token (issuer, audience of this service, signature).
            principal = request.app.state.verifier.verify(token["access_token"])
        except (OAuthError, AuthError) as exc:
            return HTMLResponse(f"<h1>Login failed</h1><p>{exc}</p>", status_code=401)
        request.session["user"] = principal.subject
        request.session["roles"] = list(principal.roles)
        return RedirectResponse(request.session.pop("next", home), status_code=303)

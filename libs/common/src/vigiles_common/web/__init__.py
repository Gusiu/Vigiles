"""Server-rendered UI shared by the services: base layout, CSS, htmx and
the demo login used with AUTH_MODE=static and dev.

htmx is vendored from npm (htmx.org 2.0.4, 0BSD licence) - no CDN, the UI
works without internet access.
"""

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader
from starlette.middleware.sessions import SessionMiddleware

from vigiles_common.auth.core import DEMO_USERS, Principal
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


def install_web(app: FastAPI, templates: Jinja2Templates, title: str, session_secret: str,
                nav: list[tuple[str, str, str]], home: str) -> None:
    """Session, static files, login/logout and HTML error pages.

    ``nav`` lists (label, path, permission) for the menu.
    """
    app.add_middleware(SessionMiddleware, secret_key=session_secret, same_site="lax", https_only=False)
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")
    templates.env.globals.update(service_title=title, nav=nav)

    def render(request: Request, name: str, status: int = 200, **context: Any) -> HTMLResponse:
        user = request.session.get("user")
        principal = None
        if user in DEMO_USERS:
            principal = Principal.of(user, DEMO_USERS[user], "user")
        return templates.TemplateResponse(request, name, {"user": principal, **context}, status_code=status)

    app.state.render = render

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, _: LoginRequired) -> RedirectResponse:
        return RedirectResponse(f"/login?next={request.url.path}", status_code=303)

    @app.exception_handler(UiForbidden)
    async def _forbidden(request: Request, exc: UiForbidden) -> HTMLResponse:
        return render(request, "forbidden.html", status=403, detail=exc.detail)

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse(home, status_code=303)

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

    @app.post("/logout", include_in_schema=False)
    def logout(request: Request) -> RedirectResponse:
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

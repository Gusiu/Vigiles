"""Registry UI for managers: assets, work orders, conflicts, incidents, imports."""

import json
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from vigiles_common.problems import ProblemError
from vigiles_common.web import install_web, oidc_login_for, templates_for, ui_user
from vigiles_registry import conflicts, work_orders
from vigiles_registry.settings import RegistrySettings

NAV = [
    ("Assets", "/ui/assets", "assets:read"),
    ("Work orders", "/ui/work-orders", "work-orders:create"),
    ("Conflicts", "/ui/conflicts", "conflicts:read"),
    ("Incidents", "/ui/incidents", "incidents:read"),
    ("Imports", "/ui/imports", "imports:read"),
]
TECHNICIANS = ["jan.technik"]
REASONS = ["przeglad_okresowy", "usterka_urzadzenia", "czyszczenie", "brak_lacznosci"]


def install_ui(app: FastAPI, settings: RegistrySettings) -> None:
    templates = templates_for(Path(__file__).parent / "templates")
    install_web(app, templates, "Registry", settings.session_secret, NAV, home="/ui/assets",
                oidc=oidc_login_for(settings, "registry-ui", "oidc_ui_secret_registry"))
    render = app.state.render

    def db():
        return app.state.db

    @app.get("/ui/assets", response_class=HTMLResponse, include_in_schema=False)
    def assets(request: Request) -> HTMLResponse:
        ui_user(request, "assets:read")
        with db().transaction() as conn:
            rows = conn.execute(
                """
                SELECT a.*, z.code AS zone_code,
                       EXISTS (SELECT 1 FROM conflicts c WHERE c.entity_id = a.id AND c.status = 'open') AS in_conflict
                FROM assets a LEFT JOIN zones z ON z.id = a.zone_id ORDER BY a.code
                """
            ).fetchall()
        return render(request, "assets.html", assets=rows)

    @app.post("/ui/assets/{asset_id}/notes", include_in_schema=False)
    def asset_notes(request: Request, asset_id: str, notes: str = Form("")) -> RedirectResponse:
        principal = ui_user(request, "assets:write")
        with db().transaction(origin=principal.subject) as conn:
            conn.execute(
                "UPDATE assets SET notes = %s, updated_by = %s WHERE id = %s AND notes IS DISTINCT FROM %s",
                (notes or None, principal.subject, asset_id, notes or None),
            )
        return RedirectResponse("/ui/assets", status_code=303)

    @app.get("/ui/work-orders", response_class=HTMLResponse, include_in_schema=False)
    def orders(request: Request, message: str | None = None) -> HTMLResponse:
        ui_user(request, "work-orders:create")
        with db().transaction() as conn:
            rows = conn.execute(
                """
                SELECT w.*, a.code AS asset_code FROM work_orders w JOIN assets a ON a.id = w.asset_id
                ORDER BY w.created_at DESC LIMIT 200
                """
            ).fetchall()
            codes = [r["code"] for r in conn.execute("SELECT code FROM assets ORDER BY code").fetchall()]
        return render(request, "work_orders.html", orders=rows, codes=codes, reasons=REASONS,
                      technicians=TECHNICIANS, message=message)

    @app.post("/ui/work-orders", include_in_schema=False)
    def create_order(
        request: Request,
        asset_code: str = Form(...),
        reason: str = Form(...),
        assigned_to: str = Form(""),
        due_date: str = Form(""),
        description: str = Form(""),
    ) -> RedirectResponse:
        principal = ui_user(request, "work-orders:create")
        try:
            with db().transaction(origin=principal.subject) as conn:
                order, created = work_orders.create_work_order(
                    conn, asset_code=asset_code, reason=reason, assigned_to=assigned_to or None,
                    due_date=date.fromisoformat(due_date) if due_date else None,
                    description=description or None, source="ui", created_by=principal.subject,
                )
            message = "Work order created" if created else "An open work order already exists for this asset and reason"
        except (ProblemError, ValueError) as exc:
            message = f"Not created: {getattr(exc, 'detail', None) or exc}"
        return RedirectResponse(f"/ui/work-orders?message={message}", status_code=303)

    @app.post("/ui/work-orders/{order_id}/assign", include_in_schema=False)
    def assign(request: Request, order_id: str, assigned_to: str = Form(""), due_date: str = Form("")) -> RedirectResponse:
        principal = ui_user(request, "work-orders:assign")
        with db().transaction(origin=principal.subject) as conn:
            work_orders.assign_work_order(conn, order_id, assigned_to or None,
                                          date.fromisoformat(due_date) if due_date else None, principal.subject)
        return RedirectResponse("/ui/work-orders?message=Saved", status_code=303)

    @app.post("/ui/work-orders/{order_id}/cancel", include_in_schema=False)
    def cancel(request: Request, order_id: str) -> RedirectResponse:
        principal = ui_user(request, "work-orders:assign")
        with db().transaction(origin=principal.subject) as conn:
            work_orders.cancel_work_order(conn, order_id, principal.subject)
        return RedirectResponse("/ui/work-orders?message=Cancelled", status_code=303)

    @app.get("/ui/conflicts", response_class=HTMLResponse, include_in_schema=False)
    def conflict_list(request: Request, message: str | None = None) -> HTMLResponse:
        ui_user(request, "conflicts:read")
        with db().transaction() as conn:
            open_ = conflicts.list_conflicts(conn, "open")
            resolved = conflicts.list_conflicts(conn, "resolved")[-20:]
        return render(request, "conflicts.html", open_conflicts=open_, resolved=resolved, message=message,
                      show=lambda v: json.dumps(v, ensure_ascii=False) if v is not None else "-")

    @app.post("/ui/conflicts/{conflict_id}/resolve", include_in_schema=False)
    def resolve(request: Request, conflict_id: str, choice: str = Form(...), value: str = Form("")) -> RedirectResponse:
        principal = ui_user(request, "conflicts:resolve")
        if choice not in ("server", "client", "custom"):
            raise ProblemError(422, "Unprocessable Content", "invalid choice")
        with db().transaction(origin=principal.subject) as conn:
            conflicts.resolve_conflict(conn, conflict_id, choice, value or None, principal.subject)
        return RedirectResponse("/ui/conflicts?message=Conflict resolved", status_code=303)

    @app.get("/ui/incidents", response_class=HTMLResponse, include_in_schema=False)
    def incidents(request: Request) -> HTMLResponse:
        ui_user(request, "incidents:read")
        with db().transaction() as conn:
            rows = conn.execute("SELECT * FROM incidents ORDER BY created_at DESC LIMIT 200").fetchall()
        return render(request, "incidents.html", incidents=rows)

    @app.get("/ui/imports", response_class=HTMLResponse, include_in_schema=False)
    def imports(request: Request) -> HTMLResponse:
        ui_user(request, "imports:read")
        with db().transaction() as conn:
            rows = conn.execute("SELECT * FROM import_runs ORDER BY id DESC LIMIT 200").fetchall()
        return render(request, "imports.html", runs=rows)

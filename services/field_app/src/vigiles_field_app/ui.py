"""Technician UI: task list, inspection form, notes, sync status."""

from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from vigiles_common.web import install_web, oidc_login_for, templates_for, ui_user
from vigiles_field_app import actions

NAV = [("Tasks", "/ui/tasks", "tasks:read"), ("Assets", "/ui/assets", "tasks:read")]


def install_ui(app: FastAPI, settings) -> None:
    templates = templates_for(Path(__file__).parent / "templates")
    install_web(app, templates, "Field app", settings.session_secret, NAV, home="/ui/tasks",
                oidc=oidc_login_for(settings, "field-app-ui", "oidc_ui_secret_field_app"))
    render = app.state.render
    db = app.state.db
    engine = app.state.engine

    def conflicted_assets(conn) -> set[str]:
        return {r[0] for r in conn.execute("SELECT entity_id FROM pending_changes WHERE status = 'conflict'")}

    @app.get("/ui/tasks", response_class=HTMLResponse, include_in_schema=False)
    def tasks(request: Request, show: str = "mine") -> HTMLResponse:
        principal = ui_user(request, "tasks:read")
        with db.connect() as conn:
            query = """
                SELECT t.*, a.code AS asset_code, a.label FROM tasks t LEFT JOIN assets a ON a.id = t.asset_id
                WHERE t.status NOT IN ('DONE', 'CANCELLED') {mine} ORDER BY t.due_date IS NULL, t.due_date, t.title
            """.format(mine="AND t.assigned_to = ?" if show == "mine" else "")
            rows = conn.execute(query, (principal.subject,) if show == "mine" else ()).fetchall()
            done = conn.execute(
                "SELECT t.*, a.code AS asset_code FROM tasks t LEFT JOIN assets a ON a.id = t.asset_id "
                "WHERE t.status IN ('DONE', 'CANCELLED') ORDER BY t.rowid DESC LIMIT 10"
            ).fetchall()
            conflicts = conflicted_assets(conn)
        return render(request, "tasks.html", tasks=rows, done=done, show=show, conflicts=conflicts)

    @app.get("/ui/tasks/{task_id}", response_class=HTMLResponse, include_in_schema=False)
    def task(request: Request, task_id: str) -> HTMLResponse:
        ui_user(request, "tasks:read")
        with db.connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            asset = conn.execute("SELECT * FROM assets WHERE id = ?", (row["asset_id"],)).fetchone() if row else None
            inspections = conn.execute("SELECT * FROM inspections WHERE task_id = ? ORDER BY performed_at", (task_id,)).fetchall()
            conflicts = conflicted_assets(conn)
        return render(request, "task.html", task=row, asset=asset, inspections=inspections, conflicts=conflicts)

    @app.post("/ui/tasks/{task_id}/start", include_in_schema=False)
    def start(request: Request, task_id: str) -> RedirectResponse:
        ui_user(request, "inspections:write")
        actions.start_task(db, task_id)
        engine.wake.set()
        return RedirectResponse(f"/ui/tasks/{task_id}", status_code=303)

    @app.post("/ui/tasks/{task_id}/inspection", include_in_schema=False)
    def inspect(request: Request, task_id: str, result: str = Form(...), findings: str = Form("")) -> RedirectResponse:
        principal = ui_user(request, "inspections:write")
        actions.submit_inspection(db, task_id, result, findings or None, principal.subject)
        engine.wake.set()
        return RedirectResponse("/ui/tasks", status_code=303)

    @app.get("/ui/assets", response_class=HTMLResponse, include_in_schema=False)
    def assets(request: Request) -> HTMLResponse:
        ui_user(request, "tasks:read")
        with db.connect() as conn:
            rows = conn.execute("SELECT * FROM assets ORDER BY code").fetchall()
            conflicts = conflicted_assets(conn)
        return render(request, "assets.html", assets=rows, conflicts=conflicts)

    @app.post("/ui/assets/{asset_id}/notes", include_in_schema=False)
    def notes(request: Request, asset_id: str, notes: str = Form(""), back: str = Form("/ui/assets")) -> RedirectResponse:
        ui_user(request, "inspections:write")
        actions.edit_notes(db, asset_id, notes or None)
        engine.wake.set()
        return RedirectResponse(back if back.startswith("/") else "/ui/assets", status_code=303)

    @app.get("/ui/sync", response_class=HTMLResponse, include_in_schema=False)
    def sync_widget(request: Request) -> HTMLResponse:
        ui_user(request, "tasks:read")
        return render(request, "sync_status.html", status=engine.status())

    @app.post("/ui/sync/offline", include_in_schema=False)
    def toggle_offline(request: Request, offline: str = Form(...), back: str = Form("/ui/tasks")) -> RedirectResponse:
        ui_user(request, "inspections:write")
        engine.set_offline(offline == "1")
        return RedirectResponse(back if back.startswith("/") else "/ui/tasks", status_code=303)

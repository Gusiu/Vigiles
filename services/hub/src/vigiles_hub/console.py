"""Hub API and console: live events (SSE, Wire Tap), dead letters with
replay, outbox state (PLAN.md 6.3)."""

import asyncio
import json
import queue
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from vigiles_common.auth import Principal, require
from vigiles_common.problems import ProblemError
from vigiles_common.web import install_web, templates_for, ui_user

router = APIRouter(prefix="/api/v1")


@router.get("/events/stream")
async def stream(request: Request, _: Principal = Depends(require("console:read"))) -> StreamingResponse:
    broadcaster = request.app.state.broadcaster
    subscriber, recent = broadcaster.subscribe()

    async def events():
        try:
            for event in recent[-50:]:
                yield f"data: {json.dumps(event)}\n\n"
            while not await request.is_disconnected():
                try:
                    event = subscriber.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.2)
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            broadcaster.unsubscribe(subscriber)

    return StreamingResponse(events(), media_type="text/event-stream")


@router.get("/dead-letters")
def dead_letters(request: Request, _: Principal = Depends(require("dlq:read"))) -> list[dict]:
    with request.app.state.db.api_transaction() as conn:
        return jsonable_encoder(conn.execute("SELECT * FROM dead_letters ORDER BY id DESC LIMIT 500").fetchall())


def replay_dead_letter(app: FastAPI, dead_letter_id: int) -> dict:
    db = app.state.db
    with db.api_transaction() as conn:
        row = conn.execute("SELECT * FROM dead_letters WHERE id = %s", (dead_letter_id,)).fetchone()
    if row is None:
        raise ProblemError(404, "Not Found", f"dead letter {dead_letter_id} does not exist")
    if row["source_topic"].startswith("outbox:"):
        with db.api_transaction() as conn:
            conn.execute(
                "UPDATE outbox SET status = 'pending', attempts = 0, next_attempt_at = now(), last_error = NULL "
                "WHERE id = %s", (int(row["source_topic"].split(":", 1)[1]),))
            conn.execute("UPDATE dead_letters SET replayed_at = now() WHERE id = %s", (dead_letter_id,))
        app.state.events.record(None, "dlq", "dead_letter_replayed", "ok", row["message_id"], row["message_id"],
                                dead_letter_id=dead_letter_id, kind="outbox")
        return {"replayed": True, "id": dead_letter_id}
    from vigiles_hub.ingress.pipeline import DeadLettered

    try:
        app.state.ingress.process(row["source_topic"], row["raw_payload"].encode())
    except DeadLettered as exc:
        raise ProblemError(409, "Conflict", f"replay failed again: {exc}") from exc
    with db.api_transaction() as conn:
        conn.execute("UPDATE dead_letters SET replayed_at = now() WHERE id = %s", (dead_letter_id,))
    app.state.events.record(None, "dlq", "dead_letter_replayed", "ok", row["message_id"], row["message_id"],
                            dead_letter_id=dead_letter_id, kind="ingress")
    return {"replayed": True, "id": dead_letter_id}


@router.post("/dead-letters/{dead_letter_id}/replay")
def replay(dead_letter_id: int, request: Request, _: Principal = Depends(require("dlq:replay"))) -> dict:
    return replay_dead_letter(request.app, dead_letter_id)


@router.get("/outbox")
def outbox(request: Request, status: Literal["pending", "sent", "dead"] | None = None,
           _: Principal = Depends(require("console:read"))) -> list[dict]:
    query, params = "SELECT * FROM outbox", ()
    if status:
        query, params = query + " WHERE status = %s", (status,)
    with request.app.state.db.api_transaction() as conn:
        return jsonable_encoder(conn.execute(query + " ORDER BY id DESC LIMIT 500", params).fetchall())


def install_console(app: FastAPI, settings) -> None:
    templates = templates_for(Path(__file__).parent / "templates")
    install_web(app, templates, f"Hub console ({settings.hub_instance})", settings.session_secret,
                [("Console", "/ui/console", "console:read")], home="/ui/console")
    render = app.state.render

    @app.get("/ui/console", response_class=HTMLResponse, include_in_schema=False)
    def console(request: Request) -> HTMLResponse:
        principal = ui_user(request, "console:read")
        with app.state.db.api_transaction() as conn:
            counts = {r["status"]: r["count"] for r in conn.execute(
                "SELECT status, count(*) AS count FROM outbox GROUP BY status").fetchall()}
            pending = conn.execute("SELECT * FROM outbox WHERE status <> 'sent' ORDER BY id DESC LIMIT 50").fetchall()
            dead = conn.execute("SELECT * FROM dead_letters ORDER BY id DESC LIMIT 50").fetchall() \
                if principal.can("dlq:read") else []
        return render(request, "console.html", counts=counts, pending=pending, dead=dead)

    @app.post("/ui/dead-letters/{dead_letter_id}/replay", include_in_schema=False)
    def replay_ui(request: Request, dead_letter_id: int) -> RedirectResponse:
        ui_user(request, "dlq:replay")
        try:
            replay_dead_letter(app, dead_letter_id)
        except ProblemError:
            pass  # stays in the list; the console shows the new error
        return RedirectResponse("/ui/console", status_code=303)

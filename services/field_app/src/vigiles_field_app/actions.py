"""What a technician does in the field app. Each action changes the local
database and queues the matching registry change in one transaction."""

import uuid
from datetime import UTC, datetime

from vigiles_common.problems import ProblemError
from vigiles_field_app.db import LocalDb
from vigiles_field_app.sync import translator
from vigiles_field_app.sync.queue import queue_change


def _task(conn, task_id: str):
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise ProblemError(404, "Not Found", f"task {task_id} does not exist")
    return task


def start_task(db: LocalDb, task_id: str) -> None:
    with db.transaction() as conn:
        task = _task(conn, task_id)
        if task["status"] not in ("NEW", "ASSIGNED"):
            raise ProblemError(409, "Conflict", f"task is {task['status']}")
        conn.execute("UPDATE tasks SET status = 'IN_PROGRESS' WHERE id = ?", (task_id,))
        queue_change(conn, "work_order", task_id, task["server_rev"], {"status": "w_realizacji"})


def submit_inspection(db: LocalDb, task_id: str, result: str, findings: str | None, technician: str) -> str:
    if result not in translator.RESULT:
        raise ProblemError(422, "Unprocessable Content", f"result must be one of {', '.join(translator.RESULT)}")
    inspection_id = str(uuid.uuid4())
    performed_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    with db.transaction() as conn:
        task = _task(conn, task_id)
        if task["status"] in ("DONE", "CANCELLED"):
            raise ProblemError(409, "Conflict", f"task is {task['status']}")
        asset = conn.execute("SELECT * FROM assets WHERE id = ?", (task["asset_id"],)).fetchone()
        conn.execute(
            "INSERT INTO inspections (id, task_id, asset_id, result, findings, performed_at, technician) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (inspection_id, task_id, task["asset_id"], result, findings, performed_at, technician),
        )
        queue_change(conn, "inspection", inspection_id, None, {
            "work_order_id": task_id, "asset_id": task["asset_id"], "technician": technician,
            "performed_at": performed_at, "result": translator.RESULT[result], "findings": findings,
        })
        if asset is not None:
            conn.execute("UPDATE assets SET condition = ? WHERE id = ?", (result, asset["id"]))
            queue_change(conn, "asset", asset["id"], asset["server_rev"], {
                "condition": translator.RESULT[result], "last_inspected_at": performed_at,
            })
        conn.execute("UPDATE tasks SET status = 'DONE' WHERE id = ?", (task_id,))
        queue_change(conn, "work_order", task_id, task["server_rev"], {"status": "zakonczone"})
    return inspection_id


def edit_notes(db: LocalDb, asset_id: str, notes: str | None) -> None:
    with db.transaction() as conn:
        asset = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if asset is None:
            raise ProblemError(404, "Not Found", f"asset {asset_id} does not exist")
        conn.execute("UPDATE assets SET notes = ? WHERE id = ?", (notes, asset_id))
        queue_change(conn, "asset", asset_id, asset["server_rev"], {"notes": notes})

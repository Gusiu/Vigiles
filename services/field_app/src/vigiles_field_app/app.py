"""Field app (System A): API, UI and the sync worker."""

import threading
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from vigiles_common.auth.modes import build_token_provider, build_verifier
from vigiles_common.correlation import CorrelationIdMiddleware
from vigiles_common.health import health_router
from vigiles_common.logging import configure_logging
from vigiles_common.problems import install_problem_handlers
from vigiles_common.retry import RetryPolicy
from vigiles_field_app import api
from vigiles_field_app.db import LocalDb
from vigiles_field_app.settings import FieldAppSettings
from vigiles_field_app.sync.client import RegistryClient
from vigiles_field_app.sync.engine import SyncEngine


def create_app(settings: FieldAppSettings | None = None, registry_http: httpx.Client | None = None) -> FastAPI:
    settings = settings or FieldAppSettings()
    db = LocalDb(settings.field_app_db_path)
    client = RegistryClient(settings.registry_url, build_token_provider(settings, "svc-field-app"), settings.http_timeout_s, http=registry_http)
    engine = SyncEngine(db, client, RetryPolicy(base_s=settings.retry_base_s, cap_s=settings.retry_cap_s))
    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker = None
        if settings.background_workers:
            worker = threading.Thread(target=engine.run, args=(stop, settings.sync_interval_s), name="sync", daemon=True)
            worker.start()
        yield
        stop.set()
        engine.wake.set()
        if worker:
            worker.join(timeout=10)

    app = FastAPI(title="Vigiles Field App", version="4.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.engine = engine
    app.state.verifier = build_verifier(settings, audience="field-app-api")
    app.add_middleware(CorrelationIdMiddleware)
    install_problem_handlers(app)

    def ready() -> None:
        with db.connect() as conn:
            conn.execute("SELECT 1")

    # Ready means the local database works; the registry may be down and
    # the app keeps working offline.
    app.include_router(health_router({"local_database": ready}))
    app.include_router(api.router)
    from vigiles_field_app.ui import install_ui

    install_ui(app, settings)
    return app


def main() -> None:
    import uvicorn

    settings = FieldAppSettings()
    configure_logging(settings.service_name, settings.log_level, settings.log_format)
    uvicorn.run(create_app(settings), host="0.0.0.0", port=settings.field_app_port, log_config=None)

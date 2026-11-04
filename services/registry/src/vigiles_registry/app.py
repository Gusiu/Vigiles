"""Registry application (System B): API, UI and background workers."""

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from vigiles_common.auth.modes import build_verifier
from vigiles_common.correlation import CorrelationIdMiddleware
from vigiles_common.health import health_router
from vigiles_common.logging import configure_logging
from vigiles_common.problems import install_problem_handlers
from vigiles_registry import api
from vigiles_registry.db import Database
from vigiles_registry.settings import RegistrySettings


def create_app(settings: RegistrySettings | None = None) -> FastAPI:
    settings = settings or RegistrySettings()
    database = Database(settings.registry_database_url)
    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database.open()
        workers: list[threading.Thread] = []
        if settings.background_workers:
            from vigiles_registry.workers import start_workers

            workers = start_workers(settings, database, stop)
        yield
        stop.set()
        for worker in workers:
            worker.join(timeout=5)
        database.close()

    app = FastAPI(title="Vigiles Registry", version="4.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = database
    app.state.verifier = build_verifier(settings.auth_mode, settings.service_tokens)
    app.add_middleware(CorrelationIdMiddleware)
    install_problem_handlers(app)
    app.include_router(health_router({"database": database.ping}))
    app.include_router(api.router)
    from vigiles_registry.ui import install_ui

    install_ui(app, settings)
    return app


def main() -> None:
    import uvicorn

    settings = RegistrySettings()
    configure_logging(settings.service_name, settings.log_level, settings.log_format)
    uvicorn.run(create_app(settings), host="0.0.0.0", port=settings.registry_port, log_config=None)

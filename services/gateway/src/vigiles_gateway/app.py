"""Gateway process: device simulation API (localhost only, X-Api-Key) and
the store-and-forward publisher."""

import json
import threading
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header
from pydantic import BaseModel, Field

from vigiles_common.config import ServiceSettings
from vigiles_common.health import health_router
from vigiles_common.logging import configure_logging, get_logger
from vigiles_common.mqtt import MqttSettings
from vigiles_common.problems import ProblemError, install_problem_handlers
from vigiles_gateway.buffer import Buffer, Forwarder
from vigiles_gateway.vendors import alfa, beta

log = get_logger(__name__)


class GatewaySettings(ServiceSettings):
    service_name: str = "gateway"
    gateway_id: str = "gw-01"
    gateway_port: int = 8005
    gateway_bind: str = "127.0.0.1"
    gateway_api_key: str
    gateway_site_config: Path = Path("data/samples/site-config.json")
    gateway_buffer_path: Path = Path(".local/gateway.sqlite")
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_gw01_password: str


class AlarmIn(BaseModel):
    zone: str = "Z3"
    detectors: int = Field(default=2, ge=1, le=20)
    interval_s: float = Field(default=10, ge=0, le=300)


class DamperIn(BaseModel):
    device_id: str = "BETA-7F3K2"
    closing_time_s: float = 95.2


class ContaminationIn(BaseModel):
    device_id: str = "A-000124"
    value: float = 85


class BurstIn(BaseModel):
    count: int = Field(default=200, ge=1, le=10000)


class Gateway:
    def __init__(self, settings: GatewaySettings) -> None:
        self.settings = settings
        config = json.loads(settings.gateway_site_config.read_text(encoding="utf-8"))
        self.devices = [d for d in config["devices"] if d["gateway"] == settings.gateway_id]
        self.buffer = Buffer(settings.gateway_buffer_path)
        self.session = datetime.now(UTC).isoformat()
        self.forwarder = Forwarder(
            MqttSettings(settings.mqtt_host, settings.mqtt_port, settings.gateway_id, settings.mqtt_gw01_password),
            settings.gateway_id, self.buffer, self.session,
        )

    def device(self, device_id: str) -> dict[str, Any]:
        for device in self.devices:
            if device["device_id"] == device_id:
                return device
        raise ProblemError(404, "Not Found", f"device {device_id} is not configured on {self.settings.gateway_id}")

    def topic(self, vendor: str, device_id: str) -> str:
        return f"vigiles/raw/{vendor}/{self.settings.gateway_id}/{device_id}"

    def send(self, vendor: str, device_id: str, payload: bytes) -> None:
        self.buffer.enqueue(self.topic(vendor, device_id), payload)
        self.forwarder.wake.set()

    def alfa(self, device_id: str, evt: str, zone: str | None, **extra) -> None:
        self.send("alfa", device_id, alfa.message(self.buffer.next_id("alfa"), device_id, evt, datetime.now(UTC), zone, **extra))


def create_app(settings: GatewaySettings | None = None) -> FastAPI:
    settings = settings or GatewaySettings()
    gateway = Gateway(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        gateway.forwarder.start()
        yield
        gateway.forwarder.stop()

    app = FastAPI(title=f"Vigiles Gateway ({settings.gateway_id})", lifespan=lifespan)
    app.state.gateway = gateway
    install_problem_handlers(app)
    app.include_router(health_router())

    def api_key(x_api_key: str | None = Header(default=None)) -> None:
        if x_api_key != settings.gateway_api_key:
            raise ProblemError(401, "Unauthorized", "missing or wrong X-Api-Key")

    @app.post("/scenario/alarm", status_code=202, dependencies=[Depends(api_key)])
    def alarm(body: AlarmIn) -> dict:
        detectors = [d for d in gateway.devices if d["vendor"] == "alfa" and d["zone"] == body.zone][: body.detectors]
        if not detectors:
            raise ProblemError(422, "Unprocessable Content", f"no Alfa detectors in zone {body.zone}")

        def run() -> None:
            for index, device in enumerate(detectors):
                if index:
                    time.sleep(body.interval_s)
                gateway.alfa(device["device_id"], "ALARM", body.zone)

        threading.Thread(target=run, daemon=True).start()
        return {"detectors": [d["device_id"] for d in detectors], "interval_s": body.interval_s}

    @app.post("/scenario/damper-fault", status_code=202, dependencies=[Depends(api_key)])
    def damper(body: DamperIn) -> dict:
        device = gateway.device(body.device_id)
        gateway.send(device["vendor"], body.device_id,
                     beta.message(body.device_id, "fault", "DAMPER_SLOW_CLOSE", datetime.now(UTC), body.closing_time_s, "s"))
        return {"queued": 1}

    @app.post("/scenario/contamination", status_code=202, dependencies=[Depends(api_key)])
    def contamination(body: ContaminationIn) -> dict:
        device = gateway.device(body.device_id)
        gateway.alfa(device["device_id"], "TLM", device["zone"], contamination=body.value)
        return {"queued": 1}

    @app.post("/scenario/unknown-device", status_code=202, dependencies=[Depends(api_key)])
    def unknown_device() -> dict:
        # A detector installed on site but missing from the site configuration.
        gateway.alfa("A-000125", "TLM", "Z3", contamination=90)
        return {"queued": 1, "device_id": "A-000125"}

    @app.post("/scenario/burst", status_code=202, dependencies=[Depends(api_key)])
    def burst(body: BurstIn) -> dict:
        sensors = [d for d in gateway.devices if d["vendor"] == "alfa"]
        for index in range(body.count):
            device = sensors[index % len(sensors)]
            gateway.alfa(device["device_id"], "TLM", device["zone"], contamination=index % 50)  # below threshold
        return {"queued": body.count}

    @app.get("/buffer", dependencies=[Depends(api_key)])
    def buffer() -> dict:
        return {"buffered": gateway.buffer.size(), "connected": gateway.forwarder.connected.is_set()}

    return app


def main() -> None:
    import uvicorn

    settings = GatewaySettings()
    configure_logging(f"gateway-{settings.gateway_id}", settings.log_level, settings.log_format)
    uvicorn.run(create_app(settings), host=settings.gateway_bind, port=settings.gateway_port, log_config=None)

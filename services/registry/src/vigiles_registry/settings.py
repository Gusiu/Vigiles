from typing import Literal

from vigiles_common.config import ServiceSettings


class RegistrySettings(ServiceSettings):
    service_name: str = "registry"
    registry_database_url: str
    registry_port: int = 8001
    auth_mode: Literal["static", "dev", "oidc"] = "static"
    service_tokens: str = ""  # name=token,... (AUTH_MODE=static)
    session_secret: str = "change-me-session"
    field_app_url: str = "http://localhost:8002"
    notifier_interval_s: float = 2.0
    outbox_interval_s: float = 1.0
    http_timeout_s: float = 5.0
    background_workers: bool = True

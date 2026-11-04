from pathlib import Path

from vigiles_common.config import AuthSettings


class FieldAppSettings(AuthSettings):
    service_name: str = "field-app"
    field_app_db_path: Path = Path(".local/field_app.sqlite")
    field_app_port: int = 8002
    registry_url: str = "http://localhost:8001"
    sync_interval_s: float = 30.0  # reconciliation poll; webhooks trigger sync sooner
    http_timeout_s: float = 5.0
    retry_base_s: float = 1.0
    retry_cap_s: float = 30.0
    background_workers: bool = True


from vigiles_common.config import AuthSettings


class HubSettings(AuthSettings):
    service_name: str = "hub"
    hub_instance: str = "hub-1"
    hub_port: int = 8003
    hub_database_url: str
    registry_url: str = "http://localhost:8001"
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_hub_password: str
    # Aggregator (demonstration values)
    alarm_window_s: float = 60.0
    alarm_lateness_s: float = 10.0
    sweeper_interval_s: float = 5.0
    # Thresholds (demonstration values, not from a standard)
    damper_closing_time_s: float = 60.0
    contamination_pct: float = 80.0
    catalog_ttl_s: float = 60.0
    gateway_offline_grace_s: float = 20.0
    catalog_min_refresh_s: float = 2.0
    http_timeout_s: float = 5.0
    outbox_interval_s: float = 1.0
    background_workers: bool = True

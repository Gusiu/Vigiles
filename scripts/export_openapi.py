"""Export the OpenAPI contracts of the services to docs/api/*.json.

    uv run python scripts/export_openapi.py

The apps are built with static auth and no background workers; nothing
connects to a database or a broker.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKENS = "svc-field-app=x,svc-registry=x,svc-hub=x"


def main() -> None:
    from vigiles_field_app.app import create_app as field_app
    from vigiles_field_app.settings import FieldAppSettings
    from vigiles_hub.app import create_app as hub
    from vigiles_hub.settings import HubSettings
    from vigiles_registry.app import create_app as registry
    from vigiles_registry.settings import RegistrySettings

    common = {"auth_mode": "static", "service_tokens": TOKENS, "background_workers": False, "_env_file": None}
    apps = {
        "registry": registry(RegistrySettings(registry_database_url="postgresql://unused/registry", **common)),
        "field-app": field_app(FieldAppSettings(field_app_db_path=Path("/tmp/vigiles-openapi.sqlite"), **common)),
        "hub": hub(HubSettings(hub_database_url="postgresql://unused/hub", mqtt_hub_password="x", **common)),
    }
    out = ROOT / "docs" / "api"
    out.mkdir(parents=True, exist_ok=True)
    for name, app in apps.items():
        (out / f"{name}.json").write_text(json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"docs/api/{name}.json")


if __name__ == "__main__":
    main()

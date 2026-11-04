"""Registry settings and service credentials used by tests."""

SERVICE_TOKENS = "svc-field-app=t-field,svc-registry=t-registry,svc-hub=t-hub"
FIELD = {"Authorization": "Bearer t-field"}
HUB = {"Authorization": "Bearer t-hub"}
REGISTRY = {"Authorization": "Bearer t-registry"}


def registry_settings(database_url: str, **overrides):
    from vigiles_registry.settings import RegistrySettings

    values = dict(
        registry_database_url=database_url,
        auth_mode="static",
        service_tokens=SERVICE_TOKENS,
        background_workers=False,
        session_secret="test",
    )
    values.update(overrides)
    return RegistrySettings(**values)

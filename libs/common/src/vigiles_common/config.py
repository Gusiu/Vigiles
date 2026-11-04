"""Settings shared by every Vigiles process.

Each service subclasses ``ServiceSettings`` with its own ``env_prefix``.
Values come from environment variables first, then from ``.env`` in the
working directory, so the same code runs locally and in compose.
"""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class ServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "vigiles"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"


class AuthSettings(ServiceSettings):
    """Authentication settings shared by the HTTP services (AUTH_MODE)."""

    auth_mode: Literal["static", "dev", "oidc"] = "static"
    service_tokens: str = ""  # static: name=token,...
    oidc_issuer: str = ""  # compared with the iss claim
    oidc_jwks_url: str = ""  # internal address of the JWKS
    oidc_token_url: str = ""  # internal address of the token endpoint
    oidc_secret_svc_hub: str = ""
    oidc_secret_svc_field_app: str = ""
    oidc_secret_svc_registry: str = ""
    # UI login (AUTH_MODE=oidc): Authorization Code + PKCE
    oidc_public_url: str = ""  # browser-facing realm URL, e.g. http://localhost:8080/realms/vigiles
    oidc_ui_secret_registry: str = ""
    oidc_ui_secret_field_app: str = ""
    oidc_ui_secret_hub: str = ""
    session_secret: str = "change-me-session"

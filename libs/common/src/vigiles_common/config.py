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

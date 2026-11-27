"""Collector settings, from environment variables and then from .env in the current folder.

Environment variables win over .env, and an empty value counts as unset.
Validation errors never show input values, since the input holds keys.
"""

from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .keys import ApiKeys


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", env_ignore_empty=True, hide_input_in_errors=True
    )

    cta_bus_key: SecretStr
    cta_train_key: SecretStr
    cta_gtfsrt_key: SecretStr | None = None

    @model_validator(mode="after")
    def _keys_are_valid(self) -> Self:
        self.api_keys()
        return self

    def api_keys(self) -> ApiKeys:
        return ApiKeys(
            bus=self.cta_bus_key.get_secret_value(),
            train=self.cta_train_key.get_secret_value(),
            gtfsrt=None if self.cta_gtfsrt_key is None else self.cta_gtfsrt_key.get_secret_value(),
        )


class CollectorSettings(Settings):
    """Settings for a running collector, which also needs its buffer folder."""

    lamp_buffer_dir: Path


class HealthSettings(CollectorSettings):
    """Settings for the health check, which reports to the ping URL when one is set."""

    lamp_health_ping_url: SecretStr | None = None

    @model_validator(mode="after")
    def _ping_url_is_https(self) -> Self:
        if self.lamp_health_ping_url is None:
            return self
        parts = urlsplit(self.lamp_health_ping_url.get_secret_value())
        if parts.scheme != "https" or not parts.netloc:
            raise ValueError("LAMP_HEALTH_PING_URL must be an https URL (the value is not shown)")
        return self

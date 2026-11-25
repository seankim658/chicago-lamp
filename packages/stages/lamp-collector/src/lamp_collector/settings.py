"""Collector settings, from environment variables and then from .env in the current folder.

Environment variables win over .env, and an empty value counts as unset.
Validation errors never show input values, since the input holds keys.
"""

from typing import Self

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

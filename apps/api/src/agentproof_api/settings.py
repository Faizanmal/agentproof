from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", frozen=True, hide_input_in_errors=True
    )

    app_env: Literal["development", "test", "production"] = "development"
    database_url: SecretStr = SecretStr(
        # Public local-only fixture.
        # pragma: allowlist nextline secret
        "postgresql+asyncpg://agentproof:agentproof_local_only@127.0.0.1:5432/agentproof"
    )
    redis_url: SecretStr = SecretStr("redis://127.0.0.1:6379/0")
    public_app_url: str = "http://localhost:3000"
    api_internal_url: str = "http://127.0.0.1:8000"
    vultr_inference_base_url: str = "https://api.vultrinference.com/v1"
    vultr_inference_model: str = ""
    vultr_inference_api_key: SecretStr = SecretStr("")
    master_encryption_key: SecretStr = SecretStr("")
    session_signing_key: SecretStr = SecretStr("")
    artifact_storage_root: Path = Path("./artifacts")
    broker_url: str = "http://127.0.0.1:8090"
    broker_hmac_key: SecretStr = SecretStr("")
    dependency_timeout_seconds: float = Field(default=2.0, gt=0, le=10)

    @model_validator(mode="after")
    def validate_production_configuration(self) -> Self:
        if (
            self.app_env == "production"
            and "agentproof_local_only" in self.database_url.get_secret_value()
        ):
            raise ValueError(
                "Production cannot use the public local-development database credentials"
            )
        return self

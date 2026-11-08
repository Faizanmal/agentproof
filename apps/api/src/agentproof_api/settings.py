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
    inference_timeout_seconds: float = Field(default=30, ge=1, le=60)
    inference_max_completion_tokens: int = Field(default=4096, ge=64, le=8192)
    inference_max_calls: int = Field(default=4, ge=1, le=4)
    inference_total_tokens: int = Field(default=32768, ge=4096, le=65536)
    authorization_ttl_seconds: int = Field(default=2592000, ge=60, le=7776000)
    challenge_ttl_seconds: int = Field(default=1800, ge=60, le=3600)
    demo_mode_enabled: bool = False
    production_demo_enabled: bool = False
    registration_enabled: bool = True
    emergency_stop_enabled: bool = False
    demo_origins: list[str] = Field(default_factory=list)
    encryption_previous_keys: dict[int, SecretStr] = Field(default_factory=dict)
    session_lifetime_seconds: int = Field(default=900, ge=60, le=3600)
    refresh_lifetime_seconds: int = Field(default=604800, ge=600, le=2592000)
    auth_rate_limit: int = Field(default=10, ge=1, le=100)
    mail_backend: Literal["development", "smtp", "disabled"] = "development"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: SecretStr = SecretStr("")
    mail_from: str = ""
    encryption_key_version: int = Field(default=1, ge=1)
    event_stream_seconds: float = Field(default=25, gt=0, le=30)
    event_stream_concurrency_limit: int = Field(default=100, ge=1, le=1000)
    max_queued_runs: int = Field(default=1000, ge=1, le=10000)
    max_workspace_concurrent_runs: int = Field(default=1, ge=1, le=100)
    worker_lease_seconds: int = Field(default=45, ge=10, le=300)
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
        if self.app_env == "production" and not self.artifact_storage_root.is_absolute():
            raise ValueError(
                "Production ARTIFACT_STORAGE_ROOT must be an absolute private storage mount"
            )
        return self

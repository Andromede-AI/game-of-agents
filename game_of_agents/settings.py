from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    data_dir: Path = Field(default=Path(".goa_data"))
    api_token: str = Field(default="dev-token")
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8000)
    event_sink: str = Field(default="jsonl")
    convex_url: str | None = Field(default=None)
    convex_site_url: str | None = Field(default=None)
    convex_deployment: str | None = Field(default=None)
    convex_deploy_key: str | None = Field(default=None)
    convex_sync_token: str | None = Field(default=None)
    next_public_convex_url: str | None = Field(default=None)
    modal_token_id: str | None = Field(default=None)
    modal_token_secret: str | None = Field(default=None)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()

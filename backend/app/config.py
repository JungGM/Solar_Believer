from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "solar_believer"
    postgres_user: str = "solar"
    postgres_password: str = ""
    pipeline_mode: str = "disabled"
    collection_cron: str = "15 * * * *"
    forecast_cron: str = "20 2,5,8,11,14,17,20,23 * * *"
    prediction_cron: str = "30 * * * *"
    retraining_cron: str = "0 3 1 * *"
    scheduler_timezone: str = "Asia/Seoul"
    model_artifact_dir: str = "artifacts"
    collection_lookback_days: int = Field(default=2, ge=1, le=31)

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()

'''
Settings configuration with production safety validation.
'''
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Restaurant"
    app_env: str = "development"
    app_secret_key: str
    database_url: str
    admin_username: str = "admin"
    admin_password: str = "admin12345"
    cookie_secure: bool = True
    access_token_expire_minutes: int = 60 * 24

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def _validate_production_secrets(self) -> "Settings":
        if self.app_env == "production":
            if not self.app_secret_key or self.app_secret_key == "change-me":
                raise ValueError(
                    "APP_SECRET_KEY must be set to a strong random value in production. "
                    "Generate one with: python -c 'import secrets; print(secrets.token_hex(32))'"
                )
            if not self.admin_password or self.admin_password == "admin12345":
                raise ValueError(
                    "ADMIN_PASSWORD must be set to a strong value in production. "
                    "The default 'admin12345' is not allowed in production mode."
                )
            if len(self.admin_password) < 8:
                raise ValueError(
                    "ADMIN_PASSWORD must be at least 8 characters in production."
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
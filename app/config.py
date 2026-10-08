from typing import Literal

from cryptography.fernet import Fernet
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///./kanbanflow.db"
    session_secret: str = "dev-insecure-secret-change-me"
    secure_cookies: bool = False
    allowed_origins: list[str] = ["http://localhost:8000"]
    allowed_hosts: list[str] = ["localhost", "127.0.0.1", "testserver"]
    webhook_allowed_hosts: list[str] = [
        "hooks.slack.com",
        "discord.com",
        "discordapp.com",
        "webhook.office.com",
        "logic.azure.com",
        "powerautomate.com",
        "api.powerplatform.com",
    ]
    github_app_id: str | None = None
    github_app_slug: str | None = None
    github_client_id: str | None = None
    github_client_secret: str | None = None
    github_private_key: str | None = None
    github_webhook_secret: str | None = None
    github_api_url: str = "https://api.github.com"
    github_web_url: str = "https://github.com"
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str | None = None
    token_encryption_key: str | None = None
    attachments_dir: str = "data/attachments"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def validate_production_security(self):
        if self.environment != "production":
            return self
        if (
            self.session_secret
            in {
                "dev-insecure-secret-change-me",
                "change-me-to-a-long-random-string",
            }
            or len(self.session_secret) < 43
            or len(set(self.session_secret)) < 20
        ):
            raise ValueError(
                "production SESSION_SECRET must be generated with secrets.token_urlsafe(32)"
            )
        if not self.secure_cookies:
            raise ValueError("production requires SECURE_COOKIES=true")
        if not self.allowed_origins or any(
            not origin.startswith("https://") for origin in self.allowed_origins
        ):
            raise ValueError("production ALLOWED_ORIGINS must contain only https origins")
        if not self.allowed_hosts or any("*" in host for host in self.allowed_hosts):
            raise ValueError("production ALLOWED_HOSTS must contain explicit hostnames")
        if self.google_client_id:
            if not (self.google_redirect_uri or "").startswith("https://"):
                raise ValueError("production GOOGLE_REDIRECT_URI must be an https URL")
            try:
                Fernet(self.token_encryption_key or "")
            except ValueError:
                raise ValueError(
                    "production TOKEN_ENCRYPTION_KEY must come from Fernet.generate_key()"
                ) from None
        return self


settings = Settings()

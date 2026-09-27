"""Application configuration and environment validation."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    instagram_access_token: str = Field(..., min_length=1)
    instagram_user_id: str = Field(..., min_length=1)

    storage_provider: Literal["s3", "cloudinary"] = "s3"

    # AWS S3
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_region: str = "us-east-1"
    aws_s3_bucket: str | None = None
    aws_s3_prefix: str = "instagram-reels"

    # Cloudinary
    cloudinary_cloud_name: str | None = None
    cloudinary_api_key: str | None = None
    cloudinary_api_secret: str | None = None
    cloudinary_folder: str = "instagram-reels"

    graph_api_version: str = "v21.0"
    graph_api_timeout_seconds: float = 30.0

    polling_interval_seconds: float = 5.0
    max_polling_seconds: float = 300.0

    network_max_retries: int = 5
    network_backoff_base_seconds: float = 1.0
    network_backoff_max_seconds: float = 30.0

    watch_directory: str = "./videos"

    log_level: str = "INFO"

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        """Validate and normalize the logging level."""
        normalized = value.upper()

        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalized not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}")

        return normalized

    def validate_storage_credentials(self) -> None:
        """Validate credentials required by the selected storage provider."""
        if self.storage_provider == "s3":
            missing = [
                name
                for name, value in {
                    "AWS_ACCESS_KEY_ID": self.aws_access_key_id,
                    "AWS_SECRET_ACCESS_KEY": self.aws_secret_access_key,
                    "AWS_S3_BUCKET": self.aws_s3_bucket,
                }.items()
                if not value
            ]

            if missing:
                raise ValueError(
                    "Missing required S3 settings: " + ", ".join(missing)
                )

        elif self.storage_provider == "cloudinary":
            missing = [
                name
                for name, value in {
                    "CLOUDINARY_CLOUD_NAME": self.cloudinary_cloud_name,
                    "CLOUDINARY_API_KEY": self.cloudinary_api_key,
                    "CLOUDINARY_API_SECRET": self.cloudinary_api_secret,
                }.items()
                if not value
            ]

            if missing:
                raise ValueError(
                    "Missing required Cloudinary settings: "
                    + ", ".join(missing)
                )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings."""
    settings = Settings()
    settings.validate_storage_credentials()
    return settings

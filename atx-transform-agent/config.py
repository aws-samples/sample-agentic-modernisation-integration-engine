"""Configuration for ATX Transform Agent."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    host: str = "0.0.0.0"
    port: int = 8005
    storage_path: str = "/app/storage"
    github_pat: str = ""
    aws_region: str = "us-east-1"
    transformations_path: str = "/app/shared/transformation_def"
    atx_cli_path: str = "atx"
    debug: bool = False

    model_config = {"env_prefix": "ATX_TRANSFORM_"}


settings = Settings()

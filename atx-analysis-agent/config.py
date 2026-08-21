"""Configuration for ATX Analysis Agent."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Service
    host: str = "0.0.0.0"
    port: int = 8004

    # Storage
    storage_path: str = "/app/storage"

    # ATX CLI
    atx_binary: str = "atx"

    # Git — fallback PAT for private repositories when the request omits one
    github_pat: str = ""

    # AWS
    aws_region: str = "us-east-1"

    model_config = {"env_prefix": "ATX_ANALYSIS_", "env_file": ".env", "extra": "ignore"}


settings = Settings()

# Analysis type → ATX managed definition mapping
ANALYSIS_DEFINITIONS: dict[str, str] = {
    "code-assessment": "AWS/comprehensive-codebase-analysis",
}

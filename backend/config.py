"""Application configuration via Pydantic BaseSettings."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Authentication
    AUTH_DISABLED: bool = False
    LOCAL_AUTH_SECRET: str = ""
    COGNITO_USER_POOL_ID: str = ""
    COGNITO_CLIENT_ID: str = ""

    # AWS
    AWS_REGION: str = "us-east-1"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""

    # Bedrock
    BEDROCK_MODEL_ID: str = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

    # Bedrock call budget. botocore defaults the read timeout to 60s, but a
    # 4096-token documentation generation against a real analysis context
    # measures ~75s — the default turns a working call into a hard failure.
    BEDROCK_READ_TIMEOUT_SECONDS: int = 300
    BEDROCK_CONNECT_TIMEOUT_SECONDS: int = 10

    # Retry budget for Bedrock calls: 2 attempts total (one retry), exponential
    # backoff, base delay 1s. Applied only to retryable failures (timeout,
    # throttling, transient service errors) — a denied model or invalid request
    # fails fast.
    #
    # The budget is bounded because the worst case before a `failed` verdict is
    # BEDROCK_MAX_ATTEMPTS × BEDROCK_READ_TIMEOUT_SECONDS plus backoff, so the
    # two settings are chosen together rather than independently. At 5 attempts
    # a genuinely hung Bedrock cost up to ~25 minutes, and an analysis sitting
    # that long looks alive when it is not; 2 attempts caps it near 10 minutes.
    # An attempt that has already consumed the full 300s read timeout is
    # unlikely to clear on an immediate retry, so attempts 3–5 bought little
    # against that cost. The one retry is kept because it does earn its keep on
    # a throttle or a transient 5xx, which genuinely clear.
    BEDROCK_MAX_ATTEMPTS: int = 2
    BEDROCK_RETRY_BASE_DELAY_SECONDS: float = 1.0

    # Deliberately skip Phase 2 AI enrichment. Analysis still completes and all
    # deterministic results persist; enrichment reports status "skipped".
    SKIP_AI_ENRICHMENT: bool = False

    # AgentCore
    AGENTCORE_MODE: bool = False
    PROGRESS_TABLE: str = "CodeInsights-Progress"
    ANALYSIS_BUCKET: str = "code-insights-analyses"

    # Analysis
    # Queries the public OSV API during the deterministic analysis phase so upgrade
    # recommendations can cite real advisories. Set false for air-gapped environments;
    # recommendations then come from the curated rules only.
    VULN_SCAN_ENABLED: bool = True

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def cors_origin_list(self) -> list[str]:
        """Parse CORS_ORIGINS comma-separated string into a list."""
        return [
            origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()
        ]


settings = Settings()

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates xz-utils unzip \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 22+ (required by ATX CLI)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install ATX CLI
# The install script unpacks to $HOME/.local/share/atx/<version>/ (binary sits directly
# in that dir, alongside its *.index.js chunks, plugins and lambda-zips) and symlinks
# $HOME/.local/bin/atx -> that binary.
# We resolve the versioned dir via the installer's own symlink instead of hardcoding the
# version, then copy the ENTIRE directory (the binary alone cannot run without siblings).
# readlink -e + test -x fail the build if the symlink is missing, so an empty /opt/atx
# can never slip through.
# `atx --version` is the build-time gate: it proves the binary executes and resolves its
# chunk files. The CLI refuses to start unless *some* AWS credentials are loadable (it
# does not validate them for --version), so throwaway values are supplied inline for this
# single layer only — they are not ENV and do not persist into the image. Real credentials
# are injected at runtime.
RUN curl -fsSL https://transform-cli.awsstatic.com/install.sh | bash \
    && ATX_BIN="$(readlink -e /root/.local/bin/atx)" \
    && test -x "$ATX_BIN" \
    && mkdir -p /opt/atx \
    && cp -r "$(dirname "$ATX_BIN")"/. /opt/atx/ \
    && test -x "/opt/atx/$(basename "$ATX_BIN")" \
    && ln -sf "/opt/atx/$(basename "$ATX_BIN")" /usr/local/bin/atx \
    && AWS_ACCESS_KEY_ID=build-probe AWS_SECRET_ACCESS_KEY=build-probe AWS_DEFAULT_REGION=us-east-1 atx --version

# Install uv for dependency management
# .NET 10 SDK for AWS/dotnet-modernization: the agent builds the ported solution to
# validate it. libicu is required by the SDK on Debian slim images.
ENV DOTNET_ROOT=/usr/share/dotnet DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       "$(apt-cache search --names-only '^libicu[0-9]+$' | cut -d' ' -f1 | head -1)" \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh \
    && bash /tmp/dotnet-install.sh --channel 10.0 --install-dir "$DOTNET_ROOT" \
    && rm /tmp/dotnet-install.sh \
    && ln -sf "$DOTNET_ROOT/dotnet" /usr/local/bin/dotnet \
    && dotnet --list-sdks
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install Python dependencies
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

# Copy application code
COPY . .

# Create storage directories and non-root user
RUN mkdir -p /app/storage /app/shared/transformation_def \
    && groupadd --system --gid 1000 appuser \
    && useradd --system --uid 1000 --gid 1000 --home-dir /app appuser \
    && chown -R appuser:appuser /app /opt/atx

USER appuser

EXPOSE 8005

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8005/health')"

CMD ["uv", "run", "--no-sync", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8005"]

FROM --platform=linux/arm64 python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN groupadd --system --gid 1000 appuser && useradd --system --uid 1000 --gid 1000 --home-dir /app appuser && mkdir -p /app/temp && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
# Single-quote the URL inside the double-quoted -c argument (same form as the
# ATX agent Dockerfiles). Nesting double quotes lets the shell strip the inner
# pair, leaving Python a SyntaxError and the container unhealthy forever.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

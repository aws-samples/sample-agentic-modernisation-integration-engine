.PHONY: install install-dev test lint format run lock export clean

install:
	uv sync --no-dev

install-dev:
	uv sync

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

run:
	uv run uvicorn main:app --reload --port 8005

lock:
	uv lock

export:
	uv export --frozen --no-hashes --no-dev -o requirements.txt

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true

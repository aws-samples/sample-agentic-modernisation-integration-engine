.PHONY: install install-dev test lint format run clean lock export

# Canonical: all Python runs through uv, against pyproject.toml + uv.lock.

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

run:
	uv run uvicorn main:app --reload --port 8000

lock:
	uv lock

export:
	uv export --frozen --no-hashes --no-dev -o requirements.txt

clean:
	find ../temp_scripts -mindepth 1 ! -name .gitkeep ! -name .gitignore -delete 2>/dev/null || true

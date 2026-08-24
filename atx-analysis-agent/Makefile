.PHONY: install install-dev test lint format run lock export

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
	uv run uvicorn main:app --reload --port 8004

lock:
	uv lock

export:
	uv export --frozen --no-hashes --no-dev -o requirements.txt

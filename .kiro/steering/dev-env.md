---
inclusion: always
---
# Development Environment

## Python (backend/)

- **Python 3.11** via `uv` (venv at `backend/.venv`).
- Source of truth: `backend/pyproject.toml`. Lock: `backend/uv.lock`.
- `backend/requirements.txt` is generated for Docker only (`make export`). Never hand-edit.
- ALL execution uses `uv run` — never bare `python`, `pytest`, `pip install`, or `ruff`.
- **CRITICAL**: To lint or format, use `make lint` / `make format` (which run `uv run ruff`). NEVER call `ruff` directly — it is not installed globally, only inside the uv-managed venv.

| Command (from `backend/`) | Purpose |
|---|---|
| `make install-dev` | Sync full dev environment |
| `make test` | Run pytest |
| `make lint` | Run ruff check + ruff format --check |
| `make format` | Auto-format with ruff |
| `make run` | Start uvicorn dev server (port 8000) |
| `make lock` | Refresh uv.lock after dep changes |
| `make export` | Regenerate requirements.txt |

- Adding a dep = edit `pyproject.toml` → `make lock` → `make export`.
- Verify: `make lint && make test` must pass before marking a task complete.

## Node (frontend/)

- **Node 20 LTS**, package manager **npm**.
- Lockfile `frontend/package-lock.json` committed (Docker uses `npm ci`).
- Type checking via `tsc --noEmit` (strict mode, noUnusedLocals, noUnusedParameters).

| Command (from `frontend/`) | Purpose |
|---|---|
| `npm install` | Install deps / update lockfile |
| `npm run build` | `tsc && vite build` (production) |
| `npm run dev` | Vite dev server |
| `npm run test` | `vitest run` |
| `npm run lint` | `tsc --noEmit` |

- Verify: `npm run lint && npm run test` must pass before marking a task complete.

## Throwaway Scripts

- Location: repo-root `temp_scripts/` (gitignored).
- Run: `cd backend && uv run python ../temp_scripts/<name>.py`
- Clean: `make clean` from `backend/`.

## Full Setup Details

Infrastructure config files (docker-compose.yml, .env.example, Dockerfiles, nginx.conf, pyproject.toml, package.json, tsconfig.json, vite.config.ts) are pre-defined in `seeds/` and copied into place during Task 1. See `seeds/README.md` for the full list.

Detailed package version pins are also documented in `steering/dev-env-setup.md` (manual inclusion via `#dev-env-setup`) for reference if seeds need modification.

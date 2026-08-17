# Seed Files

These files are **deterministic infrastructure/config files** that should be copied verbatim
into the project during Task 1 (environment bootstrap). They define contracts, ports, proxy
rules, dependency versions, and Docker configuration that must not vary between generations.

## Why seed files?

Without them, an AI agent must *guess* at:
- nginx proxy routing rules (complex, error-prone — and every prefix is a path the SPA can no longer own)
- docker-compose service definitions (port numbers, volume names, health checks)
- Exact dependency versions that work together (bcrypt/passlib compatibility, boto3/langchain-aws alignment)
- TypeScript strictness settings
- Dockerfile multi-stage patterns

These are all fixed contracts — not creative implementation. Including them as seeds:
1. **Eliminates non-determinism** — every generation uses identical infra
2. **Saves ~30% of generation time** — no time spent on boilerplate config
3. **Prevents build failures** — known-good configs vs AI-guessed ones

## Usage (Task 1)

```bash
# Copy seed files into place
cp seeds/.env.example .env.example
cp seeds/.gitignore .gitignore
cp seeds/docker-compose.yml docker-compose.yml

cp seeds/backend/.python-version backend/.python-version
cp seeds/backend/pyproject.toml backend/pyproject.toml
cp seeds/backend/Makefile backend/Makefile
cp seeds/backend/Dockerfile backend/Dockerfile

mkdir -p backend/prompts
cp seeds/backend/prompts/analysis-summary.md backend/prompts/analysis-summary.md
cp seeds/backend/prompts/code-analysis-agent.md backend/prompts/code-analysis-agent.md
cp seeds/backend/prompts/documentation-generation.md backend/prompts/documentation-generation.md
cp seeds/backend/prompts/kiro-spec-generation.md backend/prompts/kiro-spec-generation.md
cp seeds/backend/prompts/quality-evaluation.md backend/prompts/quality-evaluation.md

cp seeds/frontend/package.json frontend/package.json
cp seeds/frontend/tsconfig.json frontend/tsconfig.json
cp seeds/frontend/vite.config.ts frontend/vite.config.ts
cp seeds/frontend/index.html frontend/index.html
cp seeds/frontend/Dockerfile frontend/Dockerfile
cp seeds/frontend/.dockerignore frontend/.dockerignore
cp seeds/frontend/nginx.conf frontend/nginx.conf

cp seeds/atx-analysis-agent/Dockerfile atx-analysis-agent/Dockerfile
cp seeds/atx-analysis-agent/pyproject.toml atx-analysis-agent/pyproject.toml
cp seeds/atx-transform-agent/Dockerfile atx-transform-agent/Dockerfile
cp seeds/atx-transform-agent/pyproject.toml atx-transform-agent/pyproject.toml

cp seeds/data/aws-managed-transformations.json \
   atx-transform-agent/data/aws_managed_transformations.json

# Then bootstrap environments
cd backend && uv lock && uv sync && make export   # requirements.txt for Docker
cd ../frontend && npm install
# Both agent Dockerfiles run `uv sync --no-dev --frozen`, which cannot generate
# a lock at build time — so the locks must exist before any image is built.
cd ../atx-analysis-agent && uv lock
cd ../atx-transform-agent && uv lock
```

## Contents

| File | Purpose |
|------|---------|
| `.env.example` | All environment variables with defaults |
| `.gitignore` | Standard ignores for Python/Node/Docker |
| `docker-compose.yml` | The four services this build produces — `frontend`, `backend`, `atx-analysis-agent`, `atx-transform-agent` — with ports, volumes, health checks. Every build context it names is a directory a task creates, so `docker compose up -d --build` needs no scoping |
| `backend/.python-version` | Pin Python 3.11 |
| `backend/pyproject.toml` | All Python deps with compatible version pins |
| `backend/Makefile` | Standard make targets (test, lint, format, run, lock, export) |
| `backend/Dockerfile` | ARM64 production image |
| `backend/prompts/analysis-summary.md` | Analysis-summary prompt template — carries the `{{placeholder}}` tokens enrichment context substitution depends on; copied to `backend/prompts/`, the single authoritative location |
| `backend/prompts/code-analysis-agent.md` | Code-analysis agent prompt template — same placeholder contract, same destination |
| `backend/prompts/documentation-generation.md` | Documentation-generation prompt template — same placeholder contract, same destination |
| `backend/prompts/kiro-spec-generation.md` | Kiro spec-generation prompt template — same placeholder contract, same destination |
| `backend/prompts/quality-evaluation.md` | Quality-evaluation prompt template (5-dimension LLM judge) — same placeholder contract, same destination |
| `frontend/package.json` | All Node deps with compatible version pins |
| `frontend/tsconfig.json` | Strict TypeScript config |
| `frontend/vite.config.ts` | Vite + React + Vitest config |
| `frontend/index.html` | SPA entry point |
| `frontend/Dockerfile` | Multi-stage Node build → Nginx serve |
| `frontend/.dockerignore` | Keeps host `node_modules/` + `dist/` out of the build context |
| `frontend/nginx.conf` | Reverse proxy rules — `/api/` and `/health` to the backend, `/atx/` and `/atx-transform/` to the two ATX agents, everything else served as the SPA |
| `atx-analysis-agent/Dockerfile` | ATX Analysis Agent — Node 22 + ATX CLI + Python 3.11 |
| `atx-analysis-agent/pyproject.toml` | Analysis agent deps (input to the `uv.lock` its Dockerfile requires) |
| `atx-transform-agent/Dockerfile` | ATX Transform Agent — Node 22 + ATX CLI + Python 3.11 |
| `atx-transform-agent/pyproject.toml` | Transform agent deps (input to the `uv.lock` its Dockerfile requires) |
| `data/aws-managed-transformations.json` | AWS managed transformation definitions catalog — copied to `atx-transform-agent/data/aws_managed_transformations.json`, the only location any code reads |

## Modification

If you need to change a seed file (add a dependency, change a port), edit it here in `seeds/`
and re-run generation. The seed is the source of truth for infrastructure config.

`seeds/` is the **single authority** for every file listed above.
`.kiro/steering/dev-env-setup.md` is advisory reference material only — where the two
disagree, `seeds/` wins, and the fix is to edit the seed.

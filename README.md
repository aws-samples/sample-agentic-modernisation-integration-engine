# Agentic Modernisation Integration Engine

> Autonomous AI agents that analyze, document, and modernize legacy codebases — so engineers can make decisions while agents do the work.

## Why

| Problem | What This Does |
|---------|---------------|
| Understanding a legacy codebase takes weeks of reading | Parses 6 languages, maps dependencies, generates architecture diagrams — in minutes |
| Documentation is always outdated or missing | AI generates docs grounded in actual parsed code structure, scored for quality |
| Java modernization is manual and error-prone | Runs AWS ATX transformations with real-time streaming, diff preview, and one-click PR |

## What You Get

- **Deterministic code analysis** — Tree-sitter parsing, dependency graphs, vulnerability scanning (OSV, 8 ecosystems), Mermaid diagrams. No AI guessing, just facts.
- **AI-powered documentation** — Bedrock Claude generates architecture docs from your actual code context. An LLM Judge scores quality across 5 dimensions.
- **Java transformation** — Pick a target (Java 21, Spring Boot), watch it run live via SSE, review the diff, download the result or open a PR.
- **Self-hosted & auditable** — Runs on your infrastructure. Every analysis persists, every transformation is replayable.

Built with FastAPI, React 18, Strands Agents, and AWS Bedrock.

📍 **[Roadmap](ROADMAP.md)** — see what's coming next.

## Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.11 | Backend runtime |
| Node.js | 20 LTS | Frontend tooling |
| Docker | 24+ | Container runtime (local dev) |
| uv | latest | Python package/project manager |

Optional: AWS credentials configured for Bedrock model access (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`).

## Quick Start

### Option A: Docker Compose (recommended)

```bash
cp .env.example .env
# Edit .env — set AWS credentials and desired auth mode

docker compose up -d --build
open http://localhost:3000
```

Services start on:
- Frontend: http://localhost:3000
- Backend API: http://localhost:8000
- ATX Analysis Agent: http://localhost:8004
- ATX Transform Agent: http://localhost:8005

### Option B: Manual Setup

**Backend:**

```bash
cd backend
uv lock && uv sync          # Install dependencies
make lint                   # Verify linting passes
make test                   # Run tests
make run                    # Start API on :8000
```

**Frontend:**

```bash
cd frontend
npm install                 # Install dependencies
npm run build               # Verify production build
npm run dev                 # Vite dev server on :5173
```

## Architecture Overview

```
┌─────────────────────────────────────────────────┐
│           Frontend (React 18 / Nginx :3000)     │
│  Dashboard │ Analysis │ ATX │ Transformations   │
└────────────────────────┬────────────────────────┘
                         │ Nginx reverse proxy
┌────────────────────────┴────────────────────────┐
│          Backend API (FastAPI :8000)            │
│  Tree-sitter Parsers │ Strands Agents │ SSE     │
└────────┬──────────────────────────┬─────────────┘
         │                          │
┌────────┴────────┐        ┌────────┴────────┐
│ ATX Analysis    │        │ ATX Transform   │
│ Agent :8004     │        │ Agent :8005     │
└─────────────────┘        └─────────────────┘
```

- **Frontend** — React 18 SPA with Material-UI, D3.js visualizations, SSE streaming, served via Nginx with reverse proxy routing to backend services.
- **Backend** — FastAPI with Tree-sitter multi-language parsing (Java, Python, JS, C#, C, Ab Initio), Strands AI agents for documentation generation and quality evaluation, OSV vulnerability scanning.
- **ATX Analysis Agent** — Runs AWS Application Transformation CLI for codebase assessment with real-time streaming output.
- **ATX Transform Agent** — Executes Java modernization transformations (WebLogic/WebSphere to Spring Boot, version upgrades) with diff preview and PR creation.

## Stopping Services

```bash
docker compose down          # Stop all containers
docker compose down -v       # Stop and remove volumes
```

## Development Commands

| Command | Purpose |
|---------|---------|
| `cd backend && make test` | Run backend tests (pytest) |
| `cd backend && make lint` | Ruff check + format verification |
| `cd backend && make run` | Start API locally on :8000 |
| `cd frontend && npm run build` | Production build |
| `cd frontend && npm run test` | Vitest unit tests |
| `cd frontend && npm run dev` | Vite dev server |
| `docker compose up -d --build` | Full stack (all services) |
| `docker compose logs -f backend` | Tail backend logs |

## Environment Variables

Copy `.env.example` to `.env` and configure:

- `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION` — AWS credentials for Bedrock
- `AUTH_DISABLED=true` — Skip authentication (dev mode)
- `LOCAL_AUTH_SECRET` — JWT signing key for local auth mode
- `BEDROCK_MODEL_ID` — Claude model ID (default: `us.anthropic.claude-sonnet-4-5-20250929-v1:0`)

See `.env.example` for the full list.

## Disclaimer

This repository provides sample code for **educational and demonstration purposes only**. It is not intended for direct production use without proper review, testing, and validation. Always test generated infrastructure artifacts (Terraform, Helm charts, kubectl commands) in non-production environments first. Use at your own risk — the authors are not responsible for any issues, damages, or losses that may result from using this code in production.

---

## License

This project is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file.

# Technology Stack — Code Transformation Engine

## Software 3.0 Stack Model

| Layer | Analogy | Technology |
|-------|---------|------------|
| **Programs** (prompts) | Source code | Prompt library (`data/prompt_library.json`), steering docs, agent instructions |
| **CPU** (model weights) | Processor | AWS Bedrock Claude Sonnet (inference engine) |
| **RAM** (context window) | Working memory | Analysis results, code ASTs, KB retrievals loaded per-invocation |
| **Filesystem** (external state) | Disk | S3 buckets, DynamoDB tables, EFS volumes |
| **OS** (runtime) | Platform | AgentCore Runtime (microVM) or ECS Fargate (container) |
| **Network** (protocols) | Communication | MCP (tool access), SSE (streaming to humans), REST (human → agent) |

## Backend (Python 3.11)

| Category | Technology | Purpose |
|----------|-----------|---------|
| Web framework | FastAPI + Uvicorn | Async API, SSE streaming, background tasks |
| AI agents | Strands Agents 1.0+ | Autonomous agent orchestration with tools |
| LLM access | boto3 (Bedrock Runtime) | AWS Bedrock Claude invocation |
| Code parsing | Tree-sitter | AST extraction (Java, Python, JS, C#, C) |
| Git operations | GitPython | Repository clone, branch management |
| Validation | Pydantic 2.5+ | Request/response schema validation |
| Auth | python-jose, passlib | JWT (HS256/RS256), password hashing |
| Rate limiting | slowapi | 60 req/min per IP |
| Security | defusedxml | Safe XML parsing (pom.xml, build.xml) |
| Vulnerability | requests → OSV API | CVE scanning across 8 ecosystems |

## Frontend (TypeScript)

| Category | Technology | Purpose |
|----------|-----------|---------|
| Framework | React 18 | Component-based UI |
| Language | TypeScript 5.3 | Type safety |
| Build | Vite 5.4 | Fast HMR, production builds |
| Components | Material-UI 5.14 | AWS-branded component library |
| Graphs | D3.js 7.9 | Force-directed dependency visualization |
| Charts | Recharts 2.8 | Statistical visualizations |
| Diagrams | Mermaid (via react-markdown) | Architecture diagram rendering |
| HTTP | Axios 1.5 | REST client with JWT interceptors |
| Streaming | Native Fetch API | SSE consumption with AbortController |
| Upload | react-dropzone 14.3 | Drag-and-drop file upload |
| Markdown | react-markdown + remark-gfm | AI output rendering |

## Infrastructure

| Category | Technology | Purpose |
|----------|-----------|---------|
| Containers | Docker (ARM64), Finch | Local development |
| Orchestration | Docker Compose | Multi-service local dev |
| Production (current) | AWS ECS Fargate | Container hosting |
| Production (target) | AWS Bedrock AgentCore Runtime | Serverless microVM agent hosting |
| CDN | CloudFront + WAF | Edge caching, DDoS protection |
| DNS | Route53 + ACM | Domain + TLS |
| IaC | CloudFormation (12+ stacks) | Declarative infrastructure |
| CI/CD | GitLab CI/CD | lint → build → scan → deploy → verify |
| Registry | AWS ECR | Docker image storage + vulnerability scanning |
| Storage | S3, EFS, DynamoDB | Analysis results, shared state |
| Auth | AWS Cognito | Production user management |
| Secrets | AWS Secrets Manager | PAT token encryption (prod) |
| Observability | CloudWatch, CloudTrail | Logs, metrics, API audit |

## Agent Communication Protocols

| Protocol | Transport | Use Case |
|----------|-----------|----------|
| MCP (Model Context Protocol) | In-process (internal), stdio (external) | Agent ↔ tool communication |
| SSE (Server-Sent Events) | HTTP streaming | Agent → human real-time output |
| REST | HTTP JSON | Human → agent commands |
| AgentCore Contract | HTTP `/ping` + `/invocations` | AgentCore → agent invocation — aspirational, see AgentCore Deployment |

A2A (agent-to-agent, JSON-RPC 2.0 + SSE) was listed here. It is produced by no task and its design
sketch has been removed from `design.md`; the backend orchestrates the agents over plain REST/SSE.

## Services & Ports

| Service | Port | Role in Software 3.0 |
|---------|------|---------------------|
| Frontend (Nginx) | 3000 | Human interface — renders agent output |
| Backend API | 8000 | Orchestrator — routes to specialized agents |
| ATX Analysis Agent | 8004 | Code understanding agent |
| ATX Transform Agent | 8005 | Code transformation agent |
| AgentCore (all agents) | 8080 | **Not produced by this build** — unified AgentCore Runtime port |

A build brings up exactly these four services: 3000, 8000, 8004, 8005, and `docker-compose.yml`
declares exactly those four. The rows that used to sit here for 8006–8008 and 7681–7684 are gone
along with the compose services and nginx prefixes that referenced them; `design.md` "Service
Registry" records the removal. Port 8080 is aspirational on separate grounds (see AgentCore
Deployment below).

## Development Commands

### Full Stack (Docker Compose)

`docker-compose.yml` declares exactly the four services this build produces — `frontend`, `backend`,
`atx-analysis-agent`, `atx-transform-agent` — and every build context it names is a directory a task
creates, so `up --build` needs no service list.

```bash
cp .env.example .env              # Configure credentials
docker compose up -d --build
docker compose logs -f backend    # Tail backend logs
open http://localhost:3000        # Access UI
docker compose down               # Stop
```

### Individual Agent (Local Dev)

Python execution goes through `uv` and the `Makefile` — see `#dev-env` for the full rule. Never
`python -m venv`, `pip install`, bare `pytest`, or bare `ruff`.

```bash
cd backend
make install-dev                  # uv sync (creates/updates backend/.venv)
make run                          # uvicorn dev server on port 8000
```

### Frontend
```bash
cd frontend
npm install
npm run dev                       # Vite dev server
npm run build                     # Production build
npm run test                      # Vitest
```

### AgentCore Deployment — aspirational, outside this build

AgentCore is the **target** runtime, not a current one. No task in `tasks.md` produces a per-agent
`agentcore_adapter.py`, a `Dockerfile.agentcore`, or a deployment driver; that work lives in
`.kiro/specs/code-insights-platform/tasks-agentcore.md` with the migration guide in
`agentcore-readiness.md`. Treat this subsection as a sketch of the intended path — do not implement
from it, and do not treat its absence as a build defect.

The image build and registry push below are real; the deployment step is not yet specified.

```bash
# Build ARM64 image
docker buildx build --platform linux/arm64 -t my-agent:arm64 --load ./backend/

# Push to ECR
aws ecr get-login-password | docker login --username AWS --password-stdin $ECR_REGISTRY
docker push $ECR_REGISTRY/code-transformation-engine/backend:latest

# Deploy to AgentCore — driver not specified by any task; see tasks-agentcore.md
```

### Tests
```bash
cd backend && make lint && make test           # Backend: ruff + pytest, both via uv run
cd frontend && npm run lint && npm run test    # Frontend: tsc --noEmit + vitest run
```

## Environment Variables

### Required (All Services)
| Variable | Description |
|----------|-------------|
| `AWS_ACCESS_KEY_ID` | AWS credentials (local dev only; AgentCore uses execution role) |
| `AWS_SECRET_ACCESS_KEY` | AWS credentials |
| `AWS_REGION` | Region (default: `us-east-1`) |
| `BEDROCK_MODEL_ID` | Model ID (default: `us.anthropic.claude-sonnet-4-5-20250929-v1:0`) |

### Authentication
| Variable | Description |
|----------|-------------|
| `AUTH_DISABLED` | `true` to skip auth (dev mode) |
| `COGNITO_USER_POOL_ID` | Cognito pool (production) |
| `COGNITO_CLIENT_ID` | Cognito client (production) |
| `LOCAL_AUTH_SECRET` | JWT signing key (staging) |

### AgentCore Mode
| Variable | Description |
|----------|-------------|
| `AGENTCORE_MODE` | Switches **only** token encryption — AWS Secrets Manager when `true`, base64 when `false`. Read in exactly one place, `backend/utils/encryption.py`. It does **not** move progress to DynamoDB or results to S3; no S3 or DynamoDB client exists in the backend. This build keeps all state local — see `#structure` "State Management Strategy" |
| `PROGRESS_TABLE` | **Declared but unused.** A setting in `config.py` and `.env.example` that nothing reads (default: `CodeInsights-Progress`). Belongs to the AgentCore migration (`tasks-agentcore.md`), not this build |
| `ANALYSIS_BUCKET` | **Declared but unused.** Same as `PROGRESS_TABLE` (default: `code-insights-analyses`) |

## AI Model Configuration

| Model | Use Case | Config |
|-------|----------|--------|
| Claude Sonnet 4.5 | Documentation, architecture, transformation planning | `BEDROCK_MODEL_ID` |
| Claude Sonnet 4 | Fallback, cost-sensitive operations | Configurable per-agent |
| Bedrock Knowledge Base | RAG for tech standards — **read by no service this build produces**; the agent that consumed it is produced by no task | `BEDROCK_KB_ID` |
| Bedrock Guardrails | Content filtering, PII detection | `BEDROCK_GUARDRAIL_ID` |

## Coding Conventions

- **Python**: PEP 8, type hints, async/await for I/O, Pydantic for validation
- **TypeScript**: strict mode, functional components, hooks over classes
- **Agents**: Strands framework, tool functions as decorated methods, SSE streaming
- **APIs**: FastAPI with auto-docs, explicit status codes, structured error responses
- **Security**: Never trust LLM output — validate, sanitize, require human confirmation for writes
- **State**: External by default (S3/DynamoDB); in-memory only for dev mode with explicit fallback
- **Containers**: Non-root user, ARM64, health checks, resource limits, no-new-privileges

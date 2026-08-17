# Project Structure — Code Transformation Engine

## Repository Layout

This is the layout a build produces — the whole of it. Every task in `tasks.md` is required, so there
is no second tier of paths that appear only under some conditions: a path is either below or it is not
created. When this file and `tasks.md` disagree on a path, `tasks.md` wins.

```
code-transformation-engine/
├── .kiro/                          # Kiro AI development configuration
│   ├── README.md                   # Reference guide for specs and project
│   ├── steering/                   # Steering context for Kiro
│   │   ├── product.md              # Product vision, Software 3.0 alignment
│   │   ├── structure.md            # This file — project structure
│   │   ├── tech.md                 # Technology stack and patterns
│   │   ├── acceptance-tests.md     # Sole authority on acceptance-test coverage
│   │   ├── dev-env.md              # uv / npm command contract (inclusion: always)
│   │   ├── dev-env-setup.md        # Version pins (manual: #dev-env-setup)
│   │   └── task-execution.md       # Sequential execution rules (one task at a time)
│   └── specs/
│       └── code-insights-platform/ # Main platform specification
│           ├── requirements.md     # 20 functional requirements
│           ├── design.md           # Architecture and design
│           ├── tasks.md            # Sequential implementation task list (authoritative)
│           ├── tasks-agentcore.md  # AgentCore migration tasks (not part of this build)
│           └── agentcore-readiness.md  # AgentCore migration guide
│
├── backend/                        # Main API (FastAPI, port 8000)
│   ├── main.py                     # FastAPI app, middleware, router registration
│   ├── config.py                   # Pydantic BaseSettings — all env vars
│   ├── state.py                    # App state singletons
│   ├── models.py                   # Pydantic request/response models
│   ├── routes/                     # The HTTP surface — all endpoints live here
│   │   ├── __init__.py
│   │   ├── analysis.py            # Upload/GitHub analysis, status, results
│   │   ├── ai_streaming.py        # SSE endpoints (documentation, judge, specs)
│   │   ├── aux.py                 # Health, config, auxiliary endpoints
│   │   └── security_fix.py        # Router skeleton (Task 2); no task populates it
│   ├── agents/                    # Strands AI agents (Software 3.0 core)
│   │   ├── __init__.py
│   │   ├── doc_analysis_agent.py  # Documentation generation agent
│   │   ├── llm_judge.py          # Quality evaluation agent (5 dimensions)
│   │   ├── kiro_specs_agent.py   # Specification generation agent
│   │   └── prompt_loader.py      # Agent-side prompt template loading
│   ├── mcp/
│   │   ├── __init__.py
│   │   └── static_analysis_server.py  # Internal in-process MCP server (9 tools)
│   ├── middleware/
│   │   ├── __init__.py
│   │   ├── security.py           # AuthMiddleware, AuditLog, rate limiter
│   │   ├── auth_routes.py        # /api/auth/* endpoints
│   │   └── transformation_management.py  # CRUD for transformation defs
│   ├── parsers/                   # Tree-sitter code parsers
│   │   ├── __init__.py
│   │   ├── base_parser.py        # Abstract base (extract classes, methods, imports)
│   │   ├── java_parser.py        # Java parser
│   │   ├── python_parser.py      # Python parser
│   │   ├── javascript_parser.py  # JavaScript/TypeScript parser
│   │   ├── csharp_parser.py      # C# parser
│   │   ├── c_parser.py           # C parser
│   │   ├── abinitio_parser.py    # Ab Initio (30+ extensions)
│   │   ├── mermaid_parser.py     # Diagram generation
│   │   └── parser_manager.py     # Routes files → parsers by extension
│   ├── services/                  # Business logic services
│   │   ├── __init__.py
│   │   ├── file_analyzer.py      # Directory walk, language classification
│   │   ├── github_handler.py     # Git clone with PAT, SSRF protection
│   │   ├── dependency_analyzer.py # Import/package extraction
│   │   ├── enhanced_dependency_analyzer.py  # OSV vulnerability scanning
│   │   ├── version_analyzer.py   # Version extraction
│   │   ├── diagram_generator.py  # Mermaid diagram orchestrator
│   │   ├── code_parser_service.py # ZIP/file analysis orchestrator
│   │   └── prompt_loader.py      # Prompt template loading from prompts/
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── bedrock.py            # Bedrock client factory + invocation wrapper
│   │   ├── guardrails.py         # Input validation, injection detection, redaction
│   │   ├── storage_manager.py    # JSON persistence with TTL/cleanup
│   │   ├── progress_tracker.py   # In-memory analysis progress
│   │   ├── encryption.py         # Token encryption (base64/Secrets Manager)
│   │   ├── prompt_paths.py       # Dual-layout prompt directory resolution
│   │   └── zip_safety.py         # Zip-slip / zip-bomb defences
│   ├── data/
│   │   ├── prompt_library.json   # Versioned prompt templates
│   │   └── aws_managed_transformations.json
│   ├── prompts/                   # SINGLE authoritative location for prompt templates.
│   │                              # Dockerfile copies backend/ into /app → ships at /app/prompts.
│   │                              # A second copy at repo root MUST NOT exist.
│   ├── pyproject.toml             # Dependency source of truth (seeded)
│   ├── uv.lock                    # Resolved lock (make lock)
│   ├── requirements.txt           # Generated for Docker only (make export)
│   ├── Makefile                   # test / lint / format / run / lock / export (seeded)
│   ├── Dockerfile                 # ARM64 production image (seeded)
│   ├── .python-version            # Pins Python 3.11 (seeded)
│   ├── .dockerignore              # MUST exclude .venv/ and caches; MUST NOT exclude prompts/
│   ├── contracts.md               # Service contracts (envelope, errors, SSE, health)
│   └── tests/
│       ├── __init__.py
│       └── conftest.py + test_*.py
│
├── atx-analysis-agent/            # ATX CLI Analysis (port 8004)
│   ├── main.py                    # FastAPI app
│   ├── config.py                  # Settings
│   └── services/                  # Repository, Command, File, Storage, ConversationId
│
├── atx-transform-agent/           # ATX Transform (port 8005)
│   ├── main.py                    # FastAPI app
│   ├── config.py                  # Settings
│   ├── data/aws_managed_transformations.json
│   └── services/                  # transform_service, docker_service (repo prep only),
│                                  # file_comparison, github_pr_service, download_service,
│                                  # stdout_filter, storage_service, repo_id,
│                                  # plan_context_defaults, transformation_validation
│
├── frontend/                      # React SPA (port 3000)
│   ├── src/
│   │   ├── main.tsx              # StrictMode entry
│   │   ├── App.tsx               # Root: theme, auth, nav, routing
│   │   ├── vite-env.d.ts
│   │   ├── components/           # 18 reusable components (+ co-located .test.tsx)
│   │   ├── pages/                # 11 routed pages (+ co-located .test.tsx)
│   │   ├── contexts/             # AuthContext.tsx
│   │   ├── services/             # api.ts, authService.ts, logStore.ts
│   │   ├── types/                # index.ts, appState.ts, antToMaven.ts, designDoc.ts
│   │   ├── utils/                # markdownComponents.tsx, markdownLinks.ts
│   │   └── test/                 # setup.ts (vitest)
│   ├── e2e/                       # Playwright acceptance specs (Task 30)
│   ├── index.html                 # Seeded — carries the brand string
│   ├── package.json               # Seeded
│   ├── package-lock.json          # npm ci input
│   ├── tsconfig.json              # Seeded
│   ├── vite.config.ts             # Seeded
│   ├── playwright.config.ts
│   ├── Dockerfile                 # Seeded
│   └── nginx.conf                 # Reverse proxy routing (seeded)
│
├── seeds/                         # Verbatim infra/config copied into place by Task 1
├── docker-compose.yml             # Declares exactly the 4 services this layout produces
├── .env.example                   # All environment variables
└── README.md                      # Quick start guide (Task 28)
```

`design-doc-agent/`, `kiro-cli-agent/`, `ant-to-maven-agent/`,
`container-agents-portfolio/eks-delivery-agents/`, `backend/agents/a2a_protocol.py`,
`backend/mcp_server.py`, `frontend/src/pages/MigrationDesign.tsx`,
`frontend/src/pages/ContainerAgentPortfolio.tsx`, `deploy/stacks/*.yaml` and `.gitlab-ci.yml` are
named by no task and must not be created. They were roadmap scope until the tasks carrying them were
removed from `tasks.md`; they are not future work this layout is leaving room for, and their absence
is not a defect. `Architecture.md`, `backend/ai_service.py`, `backend/agents/kiro_cli.py`,
`backend/services/mcp_service.py`, `backend/services/prompt_service.py`, and
`backend/utils/logger.py` are named by no task either and must not be created. The Bedrock client
factory is `backend/utils/bedrock.py`; logging uses the stdlib `logging` module configured in
`main.py`.

## Architecture Patterns (Software 3.0)

### Agent Pattern
Every AI capability is implemented as an autonomous agent with:
- **Tools** — functions the agent can call (MCP, file access, API calls)
- **Context** — analysis results, code structure loaded into working memory
- **Prompts** — natural language instructions controlling behavior
- **Streaming** — SSE output for real-time human observation

### Adapter Pattern (AgentCore) — aspirational, not built
The intended AgentCore shape is a thin per-agent adapter (`agentcore_adapter.py`) translating
between the AgentCore contract (`GET /ping` + `POST /invocations`, port 8080) and the existing
FastAPI routes and service methods. **No agent has one today** and no task in `tasks.md` creates
one — the adapters are specified in `tasks-agentcore.md` and `agentcore-readiness.md`, which are
outside this build. Do not implement from this section, and do not treat the absence of
`agentcore_adapter.py` as a defect.

### Fallback Chain Pattern
Capabilities that have a degradation path:
- Auth: Cognito → Local JWT → Disabled
- Prompt template resolution: `$PROMPTS_DIR` → package root → repo root → `/app/prompts`
- Kiro spec: CLI → Bedrock → Regex parser (the CLI and Bedrock legs live in a `kiro-cli-agent/` no
  task produces, so the CLI leg is unreachable; the backend's own `kiro_specs_agent.py` is
  Bedrock-only)
- ATX Transform **repository preparation** (not the transformation itself): Docker-in-Docker
  `git clone` → direct `git clone`. The DinD leg is currently dead — `docker` is not installed in
  `atx-transform-agent/Dockerfile`, so `is_docker_available()` is always false. The transformation
  itself always runs the ATX CLI in-process via `subprocess.Popen`; it is never containerised, and
  no "file copy" fallback exists. A fallback that cannot distinguish clone success from failure is
  worse than no fallback, so any DinD leg that is revived carries the same non-zero-exit check and
  the same PAT injection as the direct clone.

### Parser Pattern
Abstract `BaseParser` with language-specific implementations (Tree-sitter). ParserManager routes files by extension. Extracted AST data (classes, methods, imports, complexity) becomes agent context.

### Pipeline Pattern — removed
A "Pipeline Pattern (Design Doc)" section was here, describing multi-stage pipelines with stage-level
retry and checklist editing between stages. It lived in `design-doc-agent/`, which appears in the
must-not-be-created list above; nothing in this build has this shape, so the pattern is gone rather
than left as intent an always-loaded file would put in front of every task.

### Component Ownership
- Page components live in `frontend/src/pages/`; reusable components in `frontend/src/components/`
- Component file names are unique repo-wide
- Pages import components as `../components/<Name>`
- Two files with the same basename in different directories is a defect, not a variant

### Dual-layout paths
Any path that must resolve both locally and in-container is candidate-based with an env override — never `.parent` hop counting — and is verified inside the running container.

## API Conventions

- REST endpoints under `/api/` (backend), `/atx/` (analysis agent), `/atx-transform/` (transform agent). Those three, plus `/health`, are the whole proxy table in `nginx.conf` — the `/design-doc/`, `/ant-to-maven/` and `/containers/` prefixes have been removed along with the services they pointed at. Every proxy prefix is a path the SPA can no longer own, so check this list before adding a route (Build Constraint 62).
- Analysis IDs: `{source}_{YYYYMMDD_HHMMSS}` (e.g., `github_20251210_145927`)
- SSE streaming for all AI operations (POST, returns `text/event-stream`)
- Health check at `/health`
- Error responses: `{"detail": string}`; success responses are domain-specific JSON with a documented envelope key per endpoint (`backend/contracts.md`)
- AgentCore's `/ping` + `/invocations` contract on port 8080 is aspirational — see the Adapter Pattern note above

## State Management Strategy

This build stores everything on the local filesystem and in process memory:

| Concern | Where |
|---|---|
| Analysis progress | In-memory dict (`utils/progress_tracker.py`) |
| Analysis results | JSON files under `/app/temp/` (`utils/storage_manager.py`, TTL cleanup) |
| ATX conversations / transform records | Agent-local filesystem under `/app/storage` (named volumes) |

`AGENTCORE_MODE=true/false` exists as a setting (`config.py`) but currently switches **only** token
encryption — Secrets Manager when true, base64 when false (`utils/encryption.py`). It does **not**
move progress to DynamoDB or results to S3; no S3 or DynamoDB client exists in the backend. The
externalised-state design is part of the AgentCore migration (`tasks-agentcore.md`), not this build.

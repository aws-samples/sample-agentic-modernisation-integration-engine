# Kiro Configuration — Code Transformation Engine

This directory contains the complete specification for generating the Code Transformation Engine platform from scratch. The spec is designed to be self-contained: an AI agent can read this directory and produce the full working application.

**This file is a map, not a plan.** It tells you which file is authoritative for what. It deliberately does not restate task numbers, the task count, the execution order, build constraints, or dependency pins — those live in exactly one place each, listed under [Sources of Authority](#sources-of-authority) below. Every past defect in this file came from restating something that then drifted.

## How to Generate the App

Use the following prompt to kick off a full regeneration:

---

**Generation Prompt:**

> Implement the Code Transformation Engine by following the spec in `.kiro/specs/code-insights-platform/tasks.md`. Its `## Tasks` list is a single ordered sequence — execute one task at a time, in that order. Never run two tasks together, and never start a task ahead of one above it in the list. For each task:
>
> 1. Read the task body in tasks.md
> 2. Implement it
> 3. Verify it before moving on — `cd backend && make lint && make test` for backend work, `cd frontend && npm run lint && npm run test` for frontend work, plus whatever the task body names
> 4. Mark it `[x]` in tasks.md, then start the next unchecked task
>
> Follow every constraint in the `## Build Constraints` section of tasks.md. Refer to the steering files (`.kiro/steering/`) for technology choices, coding conventions, project structure, execution rules, and the dev-environment contract.
>
> Start at the first unchecked entry in the `## Tasks` list of tasks.md and read the task bodies there. Do not assume a task number, how many tasks there are, or which files a task touches — tasks.md is the only source for all three. When a task consumes something an earlier task built, read that producer's real interface out of its code rather than inferring it from your task's description.

---

Progress is tracked by the spec system through `[x]` checkbox state in `tasks.md`. Do **not** create or update a `PROGRESS.md` — see Build Constraint 28, "No PROGRESS.md", in the `## Build Constraints` section of tasks.md.

## Sources of Authority

Each of these is the single authority for its subject. Where this README or any other file appears to disagree with one of them, the authority wins and the other file is the defect.

| Subject | Authority |
|---------|-----------|
| Task numbering, task bodies, execution order | `specs/code-insights-platform/tasks.md` — the `## Tasks` list, with the `## Task Dependency Graph` recording why the order is what it is |
| Which tasks are required (all of them) | `tasks.md` — `## Priority Classification` |
| Build constraints (numbered; violations cause build failures, runtime bugs, or test gaps) | `tasks.md` — `## Build Constraints` |
| Dependency versions and pins | `seeds/backend/pyproject.toml`, `seeds/frontend/package.json`, and the `## Build Constraints` section of tasks.md. `steering/dev-env-setup.md` documents pins for reference. **Never** take a pin from this README. |
| Functional requirements | `specs/code-insights-platform/requirements.md` |
| Architecture, component locations, endpoint contracts, correctness properties | `specs/code-insights-platform/design.md` |
| Acceptance-test coverage (Assertion Rules, numbered Tests) | `steering/acceptance-tests.md` — sole authority; load with `#acceptance-tests` |
| How to execute tasks — one at a time, verify-before-next, read a producer's interface from its code, edit rather than re-create | `steering/task-execution.md` |
| Dev-environment commands (`uv`, `npm`, lint, test) | `steering/dev-env.md` |
| Pre-defined infrastructure/config files copied into place by the bootstrap task | `seeds/README.md` |

## Directory Structure

```
.kiro/
├── README.md                           # This file — map of authorities
├── steering/                           # Context for AI (see inclusion modes below)
│   ├── product.md                      # Product vision, target users, capabilities
│   ├── structure.md                    # Project layout, file conventions
│   ├── tech.md                         # Technology stack, ports, commands
│   ├── dev-env.md                      # uv/npm toolchain contract, verify commands
│   ├── dev-env-setup.md                # Full pinned package definitions (reference)
│   ├── task-execution.md               # Sequential execution rules (one task at a time)
│   └── acceptance-tests.md             # Acceptance coverage contract
├── hooks/                              # Kiro IDE hooks (lint, test, validate)
└── specs/
    └── code-insights-platform/         # Full platform specification — the build program
        ├── requirements.md             # 20 functional requirements
        ├── design.md                   # System architecture, contracts, correctness properties
        ├── tasks.md                    # Ordered implementation tasks, dependency graph, Build Constraints
        ├── agentcore-readiness.md      # AgentCore migration (future phase)
        └── tasks-agentcore.md          # AgentCore-specific tasks (future)
```

## Steering Files

Inclusion mode matters when driving a rebuild: `always` files are in context automatically, `manual` files must be pulled in explicitly with `#<filename>`.

| File | Inclusion | Purpose |
|------|-----------|---------|
| `product.md` | always | Product vision, target users, capabilities, design principles |
| `structure.md` | always | Repository layout, API conventions, state management |
| `tech.md` | always | Stack details, ports, commands, coding conventions |
| `dev-env.md` | always | Python/Node toolchain contract — `uv run` only, `make lint`/`make test` verify gates |
| `task-execution.md` | always | Sequential execution — one task at a time in `tasks.md` order, verify before the next, read a producer's interface from its code, edit rather than re-create |
| `acceptance-tests.md` | **manual** (`#acceptance-tests`) | Sole authority on acceptance-test coverage — Assertion Rules and numbered Tests. Required by the acceptance-testing task. |
| `dev-env-setup.md` | **manual** (`#dev-env-setup`) | Exact pinned package versions, for reference if seeds need modification |

## Specification Overview

### What Gets Built

An AI-powered code transformation platform. The build produces exactly four services, and `docker-compose.yml` declares exactly those four. Every task in `tasks.md` is required.

| Service | Port | Purpose |
|---------|------|---------|
| Frontend (React/Nginx) | 3000 | Web UI with reverse proxy |
| Backend API (FastAPI) | 8000 | Orchestrator, parsers, AI agents |
| ATX Analysis Agent | 8004 | CLI analysis with SSE streaming |
| ATX Transform Agent | 8005 | Java modernization, diff/download |

### Execution Plan

There is no plan in this file. Read the `## Tasks` list of `specs/code-insights-platform/tasks.md` top to bottom — that list is the plan, including how many tasks there are and what order they run in. `steering/task-execution.md` governs how to execute them: one task at a time in that order, verified before the next starts.

### Deferred Work

Deferred scope is marked in place rather than listed here, so that it cannot drift out of sync with task numbering:

- No task in `tasks.md` is deferred — every one is required, and `## Priority Classification` in that file says so. Scope that was deferred had its producing task removed outright; `requirements.md` marks the requirements it left behind `[NO TASK]` or `[PARTIAL]` in place.
- AgentCore migration lives outside `tasks.md`, in `specs/code-insights-platform/tasks-agentcore.md` and `agentcore-readiness.md`.

## Key Principles

| Principle | Implementation |
|-----------|---------------|
| Prompts as programs | Versioned prompt library controls agent behavior |
| Context engineering | Analysis results + ASTs loaded per-invocation |
| Autonomous agents | Multi-step work without human step-by-step |
| Streaming observability | SSE for all AI operations |
| Fallback chains | CLI → AI → regex for every capability |
| External state | S3/DynamoDB (prod), JSON files (dev) |
| Guardrails everywhere | Input validation → Bedrock Guardrails → output sanitization |

## Technology Stack

Summary only — `steering/tech.md` is authoritative, and dependency versions come from `seeds/` (see [Sources of Authority](#sources-of-authority)).

| Layer | Technologies |
|-------|-------------|
| Frontend | React 18, TypeScript 5.3, Vite 5.4, MUI 5, D3.js 7, Recharts |
| Backend | Python 3.11, FastAPI, Tree-sitter, Strands Agents, boto3 |
| AI | AWS Bedrock Claude Sonnet, Knowledge Base RAG |
| Auth | Cognito (RS256) / Local JWT (HS256) / Disabled |
| Infrastructure | Docker Compose, ARM64, ECS Fargate |
| Security | slowapi, pyjwt, defusedxml, prompt injection detection |

## Build Constraints

The numbered Build Constraints live in the `## Build Constraints` section of `specs/code-insights-platform/tasks.md` and nowhere else. That single numbered list is the whole set; there is no second list here and none should be added. A constraint restated outside that section is a constraint that will eventually contradict it.

## Quick Start (after generation)

Bring up all four services and check them, using the same command the acceptance-testing task runs:

```bash
cp .env.example .env    # AWS credentials — required for Bedrock and the ATX CLI
docker compose up -d --build
docker compose ps                    # health status
docker compose logs -f backend       # tail logs
docker compose down                  # stop
```

Open http://localhost:3000 for the UI.

There are no convenience start scripts. `docker compose` is the only bring-up path the spec defines.

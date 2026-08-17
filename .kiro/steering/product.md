# Code Transformation Engine

AI-powered code transformation and modernization platform built on Software 3.0 principles — where natural language prompts, context engineering, and autonomous AI agents replace manual coding effort across the entire software development lifecycle.

## Product Vision

Enable organizations to understand, document, transform, and modernize legacy codebases at unprecedented speed by orchestrating specialized AI agents that operate autonomously across every SDLC phase — from analysis through architecture design to code transformation and deployment.

## Software 3.0 Alignment

This platform embodies Karpathy's Software 3.0 paradigm:

- **Context window as RAM** — Analysis results, code structure, and dependency graphs are loaded into agent context windows as the working memory for transformation decisions
- **Model weights as CPU** — AWS Bedrock Claude performs the reasoning, planning, and code generation — the "compute" happens in the model
- **Prompts as programs** — Prompt library with versioned templates controls agent behavior; editing prompts changes the program without code changes
- **Natural language as interface** — Users describe what they want (documentation, migration plan, security fixes) in natural language; agents figure out how

## Agentic SDLC Principles

The platform implements an Agentic Software Development Lifecycle where AI agents autonomously handle multi-step work between human review checkpoints:

1. **Autonomous task execution** — Agents plan, execute, and iterate without step-by-step human instruction (DocAnalysisAgent, LLMJudge, ATX Analysis, ATX Transform)
2. **Human-in-the-loop at decision points** — Humans review generated documentation, approve PRs, validate architecture decisions, and edit checklists
3. **Multi-agent orchestration** — Specialized agents collaborate: analysis feeds documentation, documentation feeds judge evaluation, judge feedback drives regeneration
4. **Tool-augmented reasoning** — Agents use tools (Tree-sitter parsing, OSV scanning, MCP, GitHub API) to ground their reasoning in real data
5. **Streaming feedback loops** — Real-time SSE streaming lets humans observe agent reasoning and intervene early
6. **Continuous quality evaluation** — LLM Judge agent scores output on 5 dimensions, creating a feedback loop for improvement

## Core Capabilities

| Capability | SDLC Phase | Agent |
|---|---|---|
| Code Analysis & Parsing | Understanding | Backend (Tree-sitter + 6 language parsers) |
| Dependency & Vulnerability Scan | Understanding | EnhancedDependencyAnalyzer (OSV API) |
| Architecture Diagrams | Understanding | MermaidParser + DiagramGenerator |
| AI Documentation | Documentation | DocAnalysisAgent (Strands) |
| Quality Evaluation | Verification | LLMJudge (Strands, 5-dimension scoring) |
| Kiro Spec Generation | Specification | KiroSpecsAgent (Bedrock only — the Kiro CLI leg lives in a service no task produces) |
| ATX Code Assessment | Assessment | ATX Analysis Agent (CLI streaming) |
| Java Modernization | Transformation | ATX Transform Agent (diff + download; PR creation is an API-only capability with no UI caller) |

Ant-to-Maven Migration, To-Be Architecture Design (Design Doc Agent), EKS Workload Delivery
(Container Agents) and Security Fix Generation were rows in this table. None is produced by any task
in `tasks.md` — the tasks that carried them have been removed, and nothing declares their services or
proxy routes any more. `requirements.md` Requirements 7, 9, 10 and 11 record the same absence against
the requirements they leave unsatisfied. This table lists what the build produces, not what the
product line aspires to.

## Target Users

- **Migration engineers** modernizing legacy Java applications at scale
- **Solutions architects** designing To-Be cloud architectures
- **Development teams** needing automated code understanding and documentation
- **Security engineers** remediating vulnerabilities with AI-assisted fixes
- **Platform engineers** deploying agents to AgentCore Runtime

## Key Integrations

| Integration | Software 3.0 Role |
|---|---|
| AWS Bedrock (Claude Sonnet) | The "CPU" — model inference for all reasoning |
| Bedrock Guardrails | Safety layer — content filtering, PII detection |
| AgentCore Runtime | Execution environment — serverless microVM agent hosting |
| AgentCore Gateway | Governance layer — policy, auth, guardrails at the edge |
| AgentCore Identity | Credential management — secure external service access |
| MCP Protocol | Tool interface — the **in-process** server the Strands agents call. The external stdio server is produced by no task |
| Tree-sitter | Code understanding — AST parsing grounds agents in real structure |
| GitHub API | Action interface — agents create PRs, clone repos autonomously |

**A2A Protocol** and **Bedrock Knowledge Base** were rows in this table. Neither has an
implementation: no task produces an A2A endpoint, agent card or task lifecycle — the backend
orchestrates the agents over plain REST/SSE — and no service this build produces reads a Knowledge
Base. The AgentCore rows above are the target runtime, not a current one; see `#tech` "AgentCore
Deployment".

## Design Principles

1. **Agents over endpoints** — Prefer autonomous agents that reason and act over dumb REST endpoints that require humans to orchestrate
2. **Context engineering over prompt engineering** — Control agent behavior by curating what context (files, analysis, history) the model sees, not just the system prompt
3. **Streaming over polling** — Real-time SSE streaming for all AI operations; humans see reasoning as it happens
4. **Fallback chains over hard failures** — Every capability has a degradation path (CLI → Bedrock → regex; MCP → built-in; Docker → git clone)
5. **External state over internal memory** — All state externalized to S3/DynamoDB for AgentCore session isolation
6. **Guardrails at every layer** — Input validation, prompt injection detection, output sanitization, Bedrock Guardrails, Gateway policies
7. **Observe everything** — Audit logs, CloudTrail, CloudWatch, Transaction Search for full agent observability

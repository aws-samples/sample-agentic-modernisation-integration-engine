# Requirements Document

## Introduction

Code Transformation Engine (formerly Code Insights Analyser) is an AI-powered microservices platform for legacy codebase understanding, documentation generation, Java modernization, and cloud migration planning. Built on Software 3.0 principles, it orchestrates specialized AI agents that autonomously handle multi-step transformation work across every SDLC phase — from analysis through architecture design to code transformation and deployment on AWS Bedrock AgentCore.

## Glossary

| Term | Definition |
|------|-----------|
| ATX | AWS Application Transformation — CLI tool for code analysis and modernization |
| Strands | AWS agent framework for orchestrating LLM-powered tools |
| MCP | Model Context Protocol — standard for AI agent tool communication |
| A2A | Agent-to-Agent protocol — Google's standard for inter-agent communication |
| SSE | Server-Sent Events — streaming protocol for real-time UI updates |
| ADR | Architecture Decision Record — documented architecture decisions |
| OSV | Open Source Vulnerabilities — Google's vulnerability database API |
| Tree-sitter | Incremental parsing library for multi-language code analysis |
| EFS | Elastic File System — AWS shared persistent storage |
| ttyd | Terminal sharing tool providing web-based terminal access |
| Mermaid | Text-based diagram syntax rendered client-side into SVG in the browser |
| AI enrichment | The Bedrock-powered phase that adds generated documentation and an executive summary to a completed deterministic analysis |

## Requirement Standing and Validation Coverage

### Standing

`tasks.md` is the authority on what a build produces. This section mirrors that into this document,
because a build reading `requirements.md` as the contract otherwise sees twenty requirements of equal
standing, half of which no task produces.

Every requirement heading below carries exactly one marker. Where a marker applies to individual
acceptance criteria rather than to the whole requirement, those criteria carry it inline.

| Marker | Meaning |
|---|---|
| **[REQUIRED]** | Every acceptance criterion is produced by a task in `tasks.md` |
| **[PARTIAL]** | Some criteria are produced by a task and some by none; the split is stated on the affected criteria |
| **[NO TASK]** | No task in `tasks.md` produces this |
| **[OUT OF BUILD]** | The work is specified in `tasks-agentcore.md`, which is not part of this build |

Markers change no requirement number and no acceptance criterion. Requirement numbers are cited
throughout `.kiro/` and stay exactly as they are.

### Validation coverage

`tasks.md` cites no requirement, in Kiro's `_Requirements: N.N` convention or any other. The only
traceability the spec carries today runs the other way: `design.md`'s `**Validates: Requirements N**`
lines on its Correctness Properties. Each requirement below therefore ends with a **Validated by**
line naming the design Property or design section that discharges it, so a task-citation pass has a
mapping to drive from. Where nothing validates a requirement, that line says so rather than leaving
the gap silent.

A **Validated by** line is a statement about the design document, not about test coverage: a named
Property is an obligation `design.md` places on an implementation, and only Properties 8–13 have a
stated test framework (`design.md` "Property-Based Testing").

## Requirements

### Requirement 1: Multi-Mode Authentication System — **[REQUIRED]**
The system SHALL support three authentication modes — Disabled (dev), Local JWT (HS256), and AWS Cognito (RS256) — auto-detected from environment variables, with JWT validation middleware applied to all protected endpoints.

**User Story:** As a platform operator, I want to switch between authentication modes (disabled for dev, local JWT for staging, Cognito for production) by changing environment variables without code changes, so that the same codebase works across all environments.

#### Acceptance Criteria
- When `AUTH_DISABLED=true`, all requests pass without token validation
- When `LOCAL_AUTH_SECRET` is set, local JWT mode activates with HS256 signing and `/api/auth/login` endpoint
- When `COGNITO_USER_POOL_ID` and `COGNITO_CLIENT_ID` are set, Cognito mode activates with RS256 JWKS validation
- Public paths (`/health`, `/docs`, `/api/auth/login`, `/api/auth/config`) bypass authentication
- Admin paths require admin role in JWT claims
- Frontend detects auth mode via `GET /api/auth/config` and renders appropriate login flow
- 401 responses trigger token clear and page reload on the frontend
- Rate limiting enforced at 60 req/min per IP on backend

**Validated by:** `design.md` Property 3 (Authentication Enforcement). The middleware ordering and the three-mode detection are also specified in `design.md` "Backend Components" → Middleware Stack.

### Requirement 2: Multi-Language Code Analysis Pipeline — **[REQUIRED]**
The system SHALL accept codebases via ZIP upload or GitHub URL and perform multi-language static analysis using Tree-sitter parsers, producing structured results including file statistics, dependency graphs, and architecture diagrams.

**User Story:** As a developer, I want to upload a ZIP or provide a GitHub URL and receive a comprehensive analysis of the codebase (file stats, dependency graph, architecture diagrams, vulnerability scan) so that I can understand a legacy codebase quickly.

#### Acceptance Criteria
- ZIP upload endpoint accepts files up to 2GB with ZIP bomb protection
- GitHub clone supports public repos and private repos via encrypted PAT token
- Tree-sitter parsers extract classes, methods, imports, and complexity for Java, Python, JavaScript, C#, C, and Ab Initio
- Background analysis tracks progress (0-100%) pollable via status endpoint
- Dependency analysis extracts package files (pom.xml, package.json, requirements.txt) with version info
- OSV API integration scans vulnerabilities across 8 ecosystems (npm, pip, maven, gradle, nuget, composer, cargo, go)
- Mermaid diagrams (class, sequence, integration) generated from parsed code
- Results persist as JSON with 7-day TTL or 50-analysis count cap with auto-cleanup
- Analysis IDs sanitized to alphanumeric + dash/underscore only
- WHEN a class, sequence, or integration diagram is generated, THE system SHALL parse the generated diagram before persisting it, and SHALL treat a parse failure as a generation failure rather than storing the diagram
- WHERE a diagram identifier is derived from source text (imports, class names, method names), THE system SHALL emit an identifier that leaves the diagram renderable for wildcard imports, generic type parameters, scope operators, constructor forms, names beginning with a digit, and diagram-language keywords
- IF no valid identifier can be derived for an element, THEN THE system SHALL omit that element and its relationships from the diagram, and SHALL exclude raw source text from the diagram in their place
- WHERE two element names reduce to the same identifier after sanitisation, THE system SHALL emit a single declaration for that identifier
- THE system SHALL render the diagram label as the original element name with whitespace collapsed to single spaces and characters that would terminate the label escaped
- IF one diagram type cannot be generated validly, THEN THE system SHALL return in its place a substitute diagram that itself parses and names the unavailable diagram type, SHALL return the remaining diagram types unchanged, and SHALL complete the analysis
- THE system SHALL cap the integration diagram at 150 dependency relationships
- WHERE a diagram is truncated at the relationship cap, THE system SHALL include a statement within the diagram that it is partial
- THE system SHALL extract the module specifier being imported with import keywords, statement terminators, quotes, and angle brackets removed
- WHERE an import is a wildcard import, THE system SHALL extract the package path of that import rather than the wildcard character

**Validated by:** `design.md` Properties 1 (Analysis ID Uniqueness), 4 (Progress Monotonicity), 6 (Concurrency Safety), 8 (Mermaid Identifier Safety), 9 (Generated Diagrams Are Always Renderable), 39 (Declared-Version Comparison), 40 (One Recommendation Per Dependency), 41 (Recommendation Fields), 49 (Deterministic Results Survive Every Enrichment Outcome). Design sections: "Mermaid Diagram Generation Contract", "Upgrade Recommendation Production", "Backend Components" → Parser System, "LLM-Integrated Analysis Pipeline".

### Requirement 3: AI Documentation Generation with Strands Agents — **[REQUIRED]**
The system SHALL generate comprehensive codebase documentation using AWS Bedrock Claude via Strands framework with real-time SSE streaming, tool-augmented generation, and quality evaluation.

**User Story:** As a developer, I want to generate AI-powered documentation for an analyzed codebase with real-time streaming output and quality scoring, so that I get high-quality documentation without manual effort.

#### Acceptance Criteria
- DocAnalysisAgent streams documentation generation as SSE events
- Agent uses tools: `analyze_codebase_context`, `generate_kiro_spec`, `validate_analysis_output`
- Agent reads analysis data from StorageManager and MCP static analysis server (in-process)
- Supports `judge_feedback` parameter for feedback-driven regeneration
- LLMJudge evaluates output on 5 dimensions: accuracy, completeness, actionability, specificity, correctness
- Judge tools: `score_dimension`, `check_json_structure`, `detect_hallucinations`
- Multiple documentation runs tracked with timestamps and individually retrievable
- KiroSpecsAgent generates Kiro specifications via in-process MCP tools
- THE generated documentation and the generated summary SHALL each contain at least three exact strings drawn from that analysis's own deterministic result, spanning at least two of: detected languages or technologies, dependency names, top-level folder names, source location
- IF the generated output asserts that no codebase was provided, THEN THE system SHALL record the run as degraded and SHALL NOT record it as completed — this is a grounding check on a call that returned, so it takes the same status as every other grounding failure below; `failed` is reserved for a call that raised
- THE system SHALL populate five context elements — file statistics, dependencies, folder structure, diagrams, source location — from the analysis under generation, and each element SHALL be non-empty wherever the deterministic result holds that data
- WHERE a prompt template cannot be loaded, THE system SHALL substitute a built-in default carrying the same `{{placeholder}}` tokens as the template it replaces, and SHALL record that the fallback was used together with every path it tried; a substituted prompt that omits those tokens is prohibited, because substituting into a prompt with no placeholders sends no analysis context to the model at all
- IF the prompt template cannot be loaded, or any of the five required context elements is missing, THEN THE system SHALL NOT report the run as completed, and SHALL NOT abort generation before invoking the model — generation proceeds and the run is recorded as degraded under the criterion below, with the returned output retained and labelled rather than discarded for having had incomplete context. Neither condition is an exception raised during the call, and neither SHALL be recorded as one
- WHEN a required context element is missing before the prompt is sent, or the grounding check on the returned output fails, THE system SHALL record the run as degraded, SHALL retain the returned output rather than discarding it, and SHALL identify in the record which check failed
- THE system SHALL determine generation success from whether the analysis context reached the model; a non-empty, well-formed, or error-free model response SHALL by itself count as no evidence of success
- WHEN a user views a single generation run or the list of generation runs, THE system SHALL display the generation status, and a degraded run SHALL be identifiable from the run list without the user opening its output
- IF documentation generation fails or is degraded, THEN THE system SHALL complete the analysis and SHALL keep the file statistics, dependency graph, vulnerability findings, and diagrams retrievable

**Validated by:** `design.md` Properties 10 (Prompt Rendering Is Total), 11 (Enrichment Status Reflects Context), 42–46 (Generated Markdown Link Resolution), 47 (Status Classification Is Total), 48 (A Succeeded Stage's Output Survives), 49 (Deterministic Results Survive), 50 (A Retry Policy Backs Off Only Where a Retry Can Help). Design sections: "AI Enrichment Status Semantics" (the authority on which status each outcome takes), "Phase 2: AI Enrichment Flow", "Agent Prompt Storage", "Generated Markdown Link Resolution", "Retry Strategies".

### Requirement 4: ATX CLI Analysis with Conversation Management — **[REQUIRED]**
The system SHALL execute AWS Application Transformation CLI analysis with real-time streaming, conversation persistence, process management, and documentation artifact serving.

**User Story:** As a migration engineer, I want to run ATX CLI analysis on repositories with real-time log streaming and the ability to pause/resume conversations, so that I can manage long-running transformation assessments.

#### Acceptance Criteria
- Analysis starts with SSE output streaming from ATX CLI execution
- Process cancellation via SIGKILL against an in-process registry keyed by conversation id (`conversation_id` → process). The registry carries **liveness only**; every durable fact about a conversation lives in that conversation's own persisted record
- Liveness is deliberately **not** recoverable from the operating system. THE system SHALL NOT determine which analyses are running by scanning process environments (`/proc/*/environ`) or any other host-level source, because losing liveness across a restart is exactly what makes a persisted `running` status with nothing tracked recognisable as the remains of a dead run — the reconciliation Build Constraint 49 requires. A scan that survived the restart would leave that reconciliation unreachable, so it is a prohibition rather than an unimplemented feature and MUST NOT be reinstated
- `ATX_ANALYSIS_ID` is injected into the CLI child environment and is read by nothing. It is not a tracking mechanism and no behaviour may be written against it
- WHERE cancellation is requested for a conversation whose persisted status is `running` with no work tracked in the current process, THE system SHALL reconcile that record to `interrupted` and SHALL report the reconciliation as a successful outcome, not as an unknown conversation — the persisted record is the authority on whether there is anything to act on
- Conversations persist at `/app/storage/{analysis_id}/` and are resumable
- Supports default ATX analysis and custom `codebase-analyzer` definition
- Documentation files served from `Documentation/*.md` in working directory
- File browsing and content reading endpoints for analysis artifacts
- Conversation list with metadata for all past analyses

**Validated by:** `design.md` Properties 12 (Request Models Accept Exactly Their Declared Fields), 13 (Repository Preparation Yields a Local Path), 14–21 (ATX Agent Streaming and Reconnect Contract), 22–25 (ATX Artifact Collection and Documentation Serving). Design sections: "Agent Service Interfaces" → ATX Analysis Agent, "Agent Request Body Contracts", "ATX Agent Streaming and Reconnect Contract", "ATX Artifact Collection and Documentation Serving".

### Requirement 5: Java Code Transformation with Diff Review and Whole-Tree Download — **[REQUIRED]**
The system SHALL modernize Java applications (WebLogic/WebSphere → Spring Boot, version upgrades) with Docker-isolated execution, line-by-line diff preview, and a whole-tree download of the transformed code.

**User Story:** As a Java developer, I want to modernize legacy Java applications (WebLogic/WebSphere → Spring Boot), review what changed as a line-by-line diff, and download the transformed tree, so that I can verify a transformation and take its output.

#### Acceptance Criteria
- Transformation runs in Docker containers with fallback to git clone + file copy
- Line-by-line diff preview with syntax highlighting, long lines wrapped rather than clipped
- Whole-tree download of the transformed code, offered as the results surface's action and the lossless artefact beside the capped diff view
- GitHub PR creation is exposed as an ATX Transform Agent API capability — the caller supplies branch name, title, and description — and is deliberately not offered as a UI action; the capability is callable directly against the agent
- PR preview is likewise an API-level capability, available before creation to a direct API caller
- Reads transformation definitions from shared EFS volume (read-only)
- Supports both AWS managed and custom transformation definitions
- Transformation history tracking

**Validated by:** `design.md` Properties 12 (Request Models), 26–29 (Transformation Identifier Resolution and Validation), 30 (The Diff Payload Carries What the Renderer Consumes), 31 (A Download Reproduces the Tree on Disk), 32 (A Record and All Its Endpoints Survive a Restart), 33 (Backfill Recovers What Is Derivable), 36 (Every Diff Entry Is Classified), 37 (Per-Category Counts Are Always Present), 38 (File Navigation Covers the Whole Collection), 51 (A Rendered Diff Row Contains Its Whole Line), 52 (The Effective Configuration Is the Caller's, Else the Definition's Default). Design sections: "ATX Transform Page Specification", "Transformation Identifier Resolution and Validation", "Transformation Configuration Defaults (`-g additionalPlanContext`)", "Transform Results Surface and Transformation Record Persistence", "Output Composition — Source Changes Versus Generated Documentation", "Agent Service Interfaces" → ATX Transform Agent (which records PR creation and PR preview as API-only capabilities with no UI caller).

### Requirement 6: Custom Transformation Definition Management — **[REQUIRED]**
The system SHALL provide CRUD operations for custom ATX transformation definitions persisted on shared EFS volume accessible by both backend and ATX Transform Agent.

**User Story:** As a platform admin, I want to create, edit, and delete custom transformation definitions through the UI, so that my team can use custom transformations alongside AWS managed ones.

#### Acceptance Criteria
- List, create, update, and delete transformation definitions via REST API
- Definitions stored in both backend volume and shared volume
- Frontend TransformationManagement page provides full CRUD UI
- Validation ensures required fields (name, description, definition content)
- AWS managed transformations displayed alongside custom ones

**Validated by:** `design.md` Properties 5 (Volume Access Control), 27 (Resolution Never Yields a Custom Record's Local Id), 28 (The Catalog Is Flat, One Entry Per Definition), 34 (Each Tab Lists Exactly Its Own Source), 35 (One Source Failing Does Not Empty the Other). Design section: "Transformation Catalog Sources of Truth".

### Requirement 7: To-Be Architecture Design Document Pipeline — **[NO TASK]**

> No task produces any criterion below. The agent, its stages and endpoints, and the
> `MigrationDesign.tsx` page were all produced only by roadmap tasks that have since been removed
> from `tasks.md`; nothing replaced them. Task 17 creates `types/designDoc.ts` — the TypeScript types
> only, not the pipeline. This build does not satisfy this requirement, and that absence is not a
> defect.

The system SHALL generate To-Be architecture documents in Korean using a 5-stage AI pipeline with Bedrock Knowledge Base RAG, producing checklist, architecture, migration plan, and validation outputs.

**User Story:** As a solutions architect, I want to upload assessment reports and get a complete To-Be architecture document (in Korean) with migration plan, ADRs, and diagrams generated automatically, so that I can accelerate cloud migration planning.

#### Acceptance Criteria
- 5 stages: Context Aggregation (20%) → Checklist (40%) → Architecture (60%) → Migration (80%) → Validation (100%)
- Stage 1 queries Bedrock Knowledge Base across 8 categories with gap analysis
- Stage 3 generates logical/physical/security/operational architecture, 10 ADRs, draw.io diagram
- Stage 4 generates Wave Plan, Cutover Plan, Validation Plan, Risk Register (top 10)
- All outputs in Korean language
- Section regeneration, version management, and checklist editing supported
- Bedrock client with exponential backoff retry (5 attempts)
- Optional MCP enrichment from AWS Documentation MCP server with graceful fallback
- Job statuses: `PROCESSING`, `COMPLETED`, `NEEDS_REVIEW`, `FAILED`

**Validated by:** nothing. No design Property, and the design sections that used to cover it have been **removed with their producing task**: the "Agent Service Interfaces" → Design Doc Agent subsection (the five endpoints) and the "Service Registry" row for port 8006 are both gone, recorded in that section's removal table as *producing task withdrawn*. Only the "Design Job" data model remains, and it describes a shape nothing produces. The five pipeline stages, the Knowledge Base categories, the ADR count and the Korean-language obligation are stated only here.

### Requirement 8: Kiro Specification Generation Agent — **[PARTIAL]**

> The backend-side agent (`kiro_specs_agent.py`) and the SSE proxy endpoint are produced by Tasks 9
> and 10. The proxy's **target** — the `kiro-cli-agent` service the backend proxies to — is produced
> by no task, so the CLI leg of the strategy chain has nothing to reach and the backend agent is
> Bedrock-only. Affected criteria are marked inline.
The system SHALL generate Kiro specifications for individual files or batches using a 3-strategy fallback chain (Kiro CLI → Bedrock Claude → Regex Parser).

**User Story:** As a developer, I want to generate Kiro specifications for source files automatically using the best available strategy (CLI, AI, or regex fallback), so that I get specs even when some tools are unavailable.

#### Acceptance Criteria
- Single file spec generation endpoint
- Batch spec generation with SSE streaming and max file limit
- Strategy chain: Kiro CLI (if available) → Bedrock Claude (with JSON extraction) → Regex Parser (static fallback) — **[PARTIAL]** the Bedrock leg is produced by Task 9; the Kiro CLI leg lives in a `kiro-cli-agent/` service **[NO TASK]** produces, so "if available" resolves to unavailable
- Backend proxies requests to Kiro CLI Agent via internal HTTP — the proxy endpoint is produced by Task 10; its target service is **[NO TASK]**
- Specs downloadable as markdown files from frontend

**Validated by:** no design Property. The one design section still covering a produced part of this requirement is "Backend Components" → Agent System (`KiroSpecsAgent`, 3 tools + MCP). The two that covered the CLI leg have been **removed with their producing task**: the "Agent Service Interfaces" → Kiro CLI Agent subsection and the "Service Registry" row for port 8007, both recorded as *producing task withdrawn*. The three-strategy fallback chain is stated only here and in `structure.md`'s Fallback Chain Pattern.

### Requirement 9: Security Fix Workflow — **[NO TASK]**

> Task 2 creates `routes/security_fix.py` as an **empty router** — a registered module with no
> endpoints. No task implements any criterion below; the roadmap task that once did has been removed
> from `tasks.md` and nothing replaced it. This build produces the empty router and nothing else, and
> that is not a defect.
The system SHALL generate security fixes for identified vulnerabilities, apply them to stored code, and create GitHub PRs with the changes.

**User Story:** As a security engineer, I want to generate AI-powered fixes for detected vulnerabilities, preview the changes, and create a PR with one click, so that I can remediate issues quickly.

#### Acceptance Criteria
- Fix generation using Bedrock Claude with vulnerability context
- Fix application to stored code files
- GitHub PR creation with generated branch, title, and description (requires PAT)
- Per-file security analysis via SSE streaming (security or transform mode)

**Validated by:** nothing. No design Property and no `design.md` section covers the security fix workflow — the endpoints, the fix-application step and the PR flow are stated only here. A task-citation pass has no validator to cite for this requirement.

### Requirement 10: Ant-to-Maven Build Migration — **[NO TASK]**

> No task produces the agent or its frontend client (`antToMavenApi.ts`); the roadmap tasks that did
> have been removed from `tasks.md`. Task 17 creates `types/antToMaven.ts` — types only. The page
> `design.md` once named for this capability, `AntToMavenPage.tsx`, was produced by no task even
> then.
The system SHALL convert Ant-based Java build systems to Maven using AI-assisted analysis and pom.xml generation.

**User Story:** As a Java developer, I want to convert legacy Ant builds to Maven by providing a repository, so that I can modernize the build system without manual pom.xml authoring.

#### Acceptance Criteria
- Accepts repository with Ant `build.xml`
- Analyzes Ant build structure and dependencies
- Generates Maven `pom.xml` using Bedrock Claude
- Provides conversion status tracking and result retrieval

**Validated by:** nothing. No design Property, and every design section that mentioned it has been removed: the "Service Registry" row for port 8008, the "Nginx Reverse Proxy Routing" row for `/ant-to-maven/` and the "Docker Volume Mapping" row for `ant-to-maven-storage` are all recorded as *producing task withdrawn*, and the "Frontend Components" row for `AntToMavenPage.tsx` was already removed on the *never existed* ground. The Ant build analysis and the pom.xml generation behaviour are stated only here.

### Requirement 11: Container-Based EKS Agents with Web Terminal — **[NO TASK]**

> No task produces the four ttyd containers, the Container Agent Portfolio page, the EKS agents page
> or the xterm.js terminal view; the roadmap tasks that did have been removed from `tasks.md`. This
> build produces none of them, and nothing in the infrastructure names them any more — the four
> `eks-*` compose services and the `/containers/…` nginx prefixes have been removed along with the
> design sections that described them.
The system SHALL provide containerized Kiro and Claude CLI agents with ttyd web terminal access for EKS workload design and delivery tasks.

**User Story:** As a DevOps engineer, I want to access Kiro and Claude CLI agents through a web terminal in the browser, so that I can design and deliver EKS workloads without local CLI setup.

#### Acceptance Criteria
- 4 agent variants: eks-delivery-kiro, eks-delivery-claude, eks-design-kiro, eks-design-claude
- ttyd provides WebSocket-based web terminal
- Agents share repos volume and external workspace mount
- Health checks verify both ttyd and steering directory availability
- Container Agent Portfolio page serves as navigation hub

**Validated by:** nothing. No design Property, and every design section that covered it has been removed with its producing task: the "Service Registry" rows for the four agents on 7681–7684 and the "Nginx Reverse Proxy Routing" row for `/containers/` are recorded as *producing task withdrawn*, and the "Frontend Components" rows for `EksDeliveryAgentsPage.tsx` and `TerminalView.tsx` were removed on the same ground in an earlier pass. The health-check obligation over both ttyd and the steering directory is stated only here.

### Requirement 12: MCP Server & Client Integration — **[PARTIAL]**

> The internal in-process MCP server is produced by Task 9. The external stdio server, the A2A
> protocol and the Agent Cards are produced by no task. Affected criteria are marked inline.
The system SHALL expose backend APIs as an MCP server for external AI agents and consume internal MCP tools for in-process agent access, plus implement A2A protocol for cross-agent communication.

**User Story:** As an AI platform integrator, I want to connect external AI agents (like Claude Desktop) to the Code Insights backend via MCP, so that analysis tools are accessible from any MCP-compatible client.

#### Acceptance Criteria
- `CodeAssessorMCPServer` (stdio) exposes 11 tools for external agents — **[NO TASK]**
- Internal MCP static analysis server provides 9 tools to Strands agents (no HTTP) — **[REQUIRED]** Task 9
- A2A protocol (JSON-RPC 2.0 + SSE) enables external agent invocation — **[NO TASK]**
- Agent Cards served at `/.well-known/agent.json` — **[NO TASK]**: no task in `tasks.md` produces this
- Task lifecycle: submitted → working → completed/failed — **[NO TASK]**, as part of A2A

**Validated by:** no design Property. Design section: "Backend Components" → MCP Servers, which restates the 9-tool internal / 11-tool stdio split. The A2A Protocol subsection that covered the JSON-RPC 2.0 + SSE transport, the Agent Card path and the task lifecycle has been **removed with its producing task** (*producing task withdrawn*), so those three criteria are now stated only here. Nothing validates the required leg either — the internal 9-tool server has a task and a design bullet but no Property.

### Requirement 13: React Frontend with Real-Time Streaming — **[PARTIAL]**

> The shell, the analysis-results surface, the ATX pages, the transform pages, the documentation
> pages and every visualization are produced by Tasks 18, 20 and 21–26. Eight of the fourteen pages
> the Pages criterion lists, and the xterm.js terminal, are produced by no task. Affected criteria
> are marked inline.
The system SHALL provide a React SPA with AWS-branded theming, 15+ pages, real-time SSE streaming displays, D3 visualizations, and Nginx reverse proxy routing.

**User Story:** As a user, I want a responsive web UI with real-time streaming output, interactive dependency graphs, and one-click access to all platform features, so that I can efficiently navigate and use the analysis tools.

#### Acceptance Criteria
- React 18 + TypeScript + Vite + Material-UI with AWS color scheme
- Left sidebar navigation with Analysis, AI Agents, Tools, Settings sections
- Pages — **[PARTIAL]**, per page: Dashboard, Bedrock Analysis, ATX Analysis, Java Transform, Transformation Management, Previous Analyses are **[REQUIRED]** (Tasks 21–26); Migration Design, IaC Generator, Container Agents, EKS Agents, Logs, Prompts, Help are **[NO TASK]**; Ant-to-Maven is **[NO TASK]** — `AntToMavenPage.tsx` appears in no task. The "15+ pages" figure in the requirement statement above is therefore not reachable by this build
- SSE streaming for documentation, judge evaluation, file analysis, Kiro CLI
- D3.js force-directed dependency graph, Recharts statistics, Mermaid diagrams
- xterm.js terminal emulator for EKS agent access — **[NO TASK]** (`TerminalView.tsx`); no task names xterm.js
- Error boundary, loading overlays, react-dropzone file upload
- Axios with JWT interceptors and AbortController for cancellable streams
- Nginx reverse proxy routes — **[PARTIAL]**: `/api/*`, `/atx/*` and `/atx-transform/*` ship in the seeded `nginx.conf`, and each is a path the SPA can no longer own (`/atx-transform` is both, which design.md records as an unresolved defect). `/design-doc/*`, `/ant-to-maven/*` and `/containers/*` are **[NO TASK]** and no longer ship: their prefixes were removed along with the services they proxied to, so they are absent rather than unreachable
- WHEN a user views analysis results, THE analysis results view SHALL render the dependency graph as one node element per graph node and one relationship element per resolved relationship
- WHEN a user views analysis results, THE analysis results view SHALL render each diagram as generated vector output containing at least one shape or text child
- WHEN a user views analysis results, THE analysis results view SHALL render a statistics chart with one mark per file extension, for 1 to 50 file extensions
- IF a visualization has data to render, THEN THE analysis results view SHALL render that visualization in place of a parenthesised count standing in for a graph, the text "placeholder", "coming soon", or "will be implemented"
- WHERE the analysis provides 1 to 20 diagram types, THE analysis results view SHALL present one control per available type, each control SHALL render its diagram, and raw diagram source SHALL be presented only in response to an explicit user action
- IF one diagram fails to render, THEN THE analysis results view SHALL keep the remaining diagram types selectable and SHALL keep the view populated
- THE rendered output of a routed page reachable within three clicks of the sidebar navigation SHALL contain each visualization the frontend provides
- THE summary figures displayed beside a visualization SHALL equal the count of elements rendered, derived from a single normalised relationship collection regardless of how the backend labels that collection
- WHEN a dependency graph of up to 5,000 nodes is displayed, THE analysis results view SHALL complete rendering within 5 seconds

**Validated by:** `design.md` Properties 26 (Identifier Validation), 29 (An Unusable Identifier Is Unselectable), 30 (The Diff Payload Carries What the Renderer Consumes), 32 (A Record and All Its Endpoints Survive a Restart), 33 (Backfill), 35 (One Source Failing Does Not Empty the Other), 36 (Every Diff Entry Is Classified), 37 (Per-Category Counts), 38 (File Navigation), 41 (Recommendation Fields), 42 (Every Link Gets Exactly One Outcome), 46 (A Cross-Document Fragment Selects the Document), 51 (A Rendered Diff Row Contains Its Whole Line), 52 (The Effective Configuration). Design sections: "Frontend Components", "Frontend Component Ownership and File Paths", "Analysis Results Tab Specifications", "DiagramViewer Render Contract", "File Navigation for a Changed-File Collection", "Diff Row Rendering — Long Lines Wrap", "Nginx Reverse Proxy Routing". Nothing validates the four visualization-rendering criteria above as stated (one node element per node, vector output per diagram, one mark per extension, the 5,000-node budget) — they are pinned by acceptance tests in `acceptance-tests.md`, not by a Property.

### Requirement 14: Production Deployment on AWS ECS — **[PARTIAL]**

> No task produces the 12 CloudFormation stacks or the GitLab CI/CD pipeline; the roadmap task that
> did has been removed from `tasks.md`. This build deploys nothing and produces no `deploy/`
> directory and no `.gitlab-ci.yml`. Local Docker Compose, the last criterion, is the only part any
> task covers (Task 30), so it is marked inline and the rest are **[NO TASK]**.
The system SHALL deploy as 10+ ECS Fargate services behind CloudFront CDN with WAF protection, managed via 12 CloudFormation stacks and GitLab CI/CD.

**User Story:** As a DevOps engineer, I want to deploy the platform to AWS ECS with automated CI/CD including security scanning and infrastructure-as-code, so that deployments are repeatable, secure, and auditable.

#### Acceptance Criteria
- GitLab CI/CD pipeline: lint → build → scan → deploy-infra → deploy-services → deploy-cdn → verify
- ARM64 Docker images pushed to ECR with vulnerability scanning
- 12 CloudFormation stacks for VPC, Cognito, Shared, EFS, 6 services, CloudFront, ALB security
- CloudFront CDN with WAF managed rules
- Route53 DNS with ACM TLS certificates
- ECS services with force-new-deployment and stability wait
- Smoke tests verify HTTP health and ECS running counts
- Local dev supported via Docker Compose or Finch — **[REQUIRED]** (Task 30 brings up four services: frontend, backend, and the two ATX agents)

**Validated by:** no design Property. Design sections that touch it: the Architecture diagram (CloudFront + WAF, Route53) and "CI/CD Quality Gates" (SAST, secret detection, Hadolint, ECR CRITICAL blocking, ECS smoke tests). The 12 CloudFormation stacks, the seven pipeline stages and the ECS force-new-deployment behaviour are stated only here.

### Requirement 15: Security & Guardrails — **[PARTIAL]**

> The application-layer and container-layer defences are produced by Tasks 3, 4, 8, 20 and the seeded
> compose file. The two supply-chain criteria run in CI/CD, which no task produces. Affected criteria
> are marked inline.
The system SHALL implement defense-in-depth security including input validation, prompt injection detection, sensitive data redaction, container hardening, and supply chain scanning.

**User Story:** As a security-conscious operator, I want the platform to enforce defense-in-depth (injection detection, data redaction, container hardening, supply chain scanning) by default, so that AI-powered features don't introduce security vulnerabilities.

#### Acceptance Criteria
- 12 regex patterns detect prompt injection attempts
- Sensitive data redaction for AWS keys, GitHub tokens, passwords, API keys, private keys
- ZIP bomb protection (2GB limit, chunk reading, timeout)
- Path traversal protection on all file operations
- All containers: `no-new-privileges:true`, services bound to `127.0.0.1`
- CORS restricted to configured origins
- SSRF guard in frontend blocks absolute URLs
- ECR scanning gates block CRITICAL CVEs in CI/CD — **[NO TASK]**
- SAST and secret detection in pipeline — **[NO TASK]**

**Validated by:** `design.md` Properties 2 (Storage Isolation), 7 (Data Redaction), 24 (Every Listed Document Is Readable Through `GET /file`), 31 (A Download Reproduces the Tree on Disk, and Nothing Else). Design sections: "Backend Components" → Middleware Stack, "CI/CD Quality Gates" (the two **[NO TASK]** criteria above), "AgentCore Security Controls" (out-of-build). Nothing validates the container-hardening criteria (`no-new-privileges:true`, `127.0.0.1` binding), the CORS restriction, or the frontend SSRF guard.

### Requirement 16: AgentCore Runtime Contract Adapter — **[OUT OF BUILD]**

> Requirements 16–20 are the AgentCore migration. None of them appears in `tasks.md` at any priority;
> the work is specified in `tasks-agentcore.md` with the migration guide in `agentcore-readiness.md`.
> This build produces no `agentcore_adapter.py`, no `Dockerfile.agentcore`, no gateway, no
> per-agent execution roles, no Identity integration and no S3/DynamoDB state, and none of those
> absences is a defect. `AGENTCORE_MODE` exists as a setting today but switches only token encryption.
The system SHALL provide an AgentCore-compatible adapter layer for each AI agent, exposing the required `/ping` GET and `/invocations` POST endpoints on port 8080 with ARM64 container packaging.

**User Story:** As a platform engineer, I want each agent to be deployable to AWS Bedrock AgentCore Runtime without code changes to the core logic, so that I can leverage serverless microVM isolation and managed scaling.

#### Acceptance Criteria
- Each agent has a `agentcore_adapter.py` exposing `/ping` (GET, returns `{"status": "healthy"}`) and `/invocations` (POST, dispatches to internal handlers)
- `/invocations` accepts JSON payload `{"input": {"action": "...", "prompt": "...", ...}}` and routes to appropriate internal handler
- Streaming responses returned as `text/event-stream` when `stream: true` in payload
- Non-streaming responses return `{"output": {"message": ..., "status": "success"}}`
- `Dockerfile.agentcore` variant for each agent: `FROM --platform=linux/arm64`, port 8080, non-root user
- Agents function identically when run locally (docker-compose) or on AgentCore Runtime

**Validated by:** no design Property. Design section: "AgentCore Adapter Pattern", which carries the reference `agentcore_adapter.py` shape. `structure.md` and `tech.md` both mark the adapter pattern aspirational and explicitly forbid treating its absence as a defect.

### Requirement 17: AgentCore Gateway with Policy-Based Routing — **[OUT OF BUILD]**
The system SHALL deploy an AgentCore Gateway fronting all agent runtimes, providing unified authentication, Bedrock Guardrails integration, and policy-based access control.

**User Story:** As a security architect, I want a single governed entry point (AgentCore Gateway) that enforces guardrails, auth policies, and request interception for all agent invocations, so that no agent can be called without policy evaluation.

#### Acceptance Criteria
- AgentCore Gateway created with HTTP protocol type
- Each agent runtime registered as a gateway target
- Gateway policy engine controls caller-to-target authorization
- Amazon Bedrock Guardrails applied via gateway policy (content filtering, PII detection)
- Request/response interceptor Lambda functions for audit logging
- Runtimes restricted to accept invocations only from the gateway (resource-based policy)
- IAM SigV4 authentication for service-to-service; JWT bearer for end-user flows

**Validated by:** no design Property. Design sections: "AgentCore Runtime Topology" (the gateway subgraph) and "AgentCore Security Controls" (the three Gateway rows and the runtime resource-based policy row). The gateway target registration and the interceptor Lambdas are stated only here.

### Requirement 18: Per-Agent IAM Execution Roles — **[OUT OF BUILD]**
The system SHALL create dedicated IAM execution roles per agent runtime with minimum-privilege permissions scoped to specific resource ARNs.

**User Story:** As a cloud security engineer, I want each agent to have its own IAM role with only the permissions it needs (specific Bedrock models, specific S3 buckets), so that a compromised agent cannot access resources beyond its scope.

#### Acceptance Criteria
- Separate execution role per agent: `CodeInsights-{AgentName}-ExecutionRole`
- Backend role: `bedrock:InvokeModel`, `s3:GetObject`/`PutObject` on analysis bucket, `secretsmanager:GetSecretValue`
- Design Doc role: `bedrock:InvokeModel`, `bedrock:Retrieve` (Knowledge Base), `s3:*` on design-doc bucket
- Kiro CLI role: `bedrock:InvokeModel`, `s3:GetObject` on shared-repos bucket
- ATX agents: `bedrock:InvokeModel`, `s3:*` on respective storage buckets, `ecr:GetAuthorizationToken`
- Confused deputy prevention: `aws:SourceArn` and `aws:SourceAccount` conditions on all trust policies
- No wildcard resource statements in any policy

**Validated by:** no design Property. Design section: "AgentCore IAM Role Structure" (the six-role table, which names the same `CodeInsights-{AgentName}-ExecutionRole` pattern). The confused-deputy conditions and the no-wildcard-resource rule are stated only here.

### Requirement 19: AgentCore Identity for External Credentials — **[OUT OF BUILD]**
The system SHALL use AgentCore Identity to manage OAuth tokens and API keys for external services (GitHub, OSV API) instead of environment variable injection.

**User Story:** As a platform operator, I want external service credentials (GitHub PAT, API keys) managed securely by AgentCore Identity rather than passed as env vars, so that credentials are never exposed in container environments or logs.

#### Acceptance Criteria
- GitHub PAT managed via AgentCore Identity OAuth credential store
- `GetWorkloadAccessToken` used to obtain time-limited tokens at invocation time
- No AWS credentials or API keys in environment variables or docker-compose
- Credential rotation handled by AgentCore Identity without redeployment
- Agents use `AgentCoreIdentityClient` SDK to fetch credentials at runtime

**Validated by:** no design Property. Design sections: "AgentCore Runtime Topology" (the Identity subgraph) and "AgentCore Security Controls" (the Identity row). `GetWorkloadAccessToken`, the OAuth credential store and the rotation-without-redeployment obligation are stated only here.

### Requirement 20: External State Store for AgentCore Session Model — **[OUT OF BUILD]**
The system SHALL externalize agent state (analysis progress, results, conversations) to S3 and DynamoDB to support AgentCore's per-session microVM isolation model.

**User Story:** As a platform engineer, I want agent state stored externally (S3 for files, DynamoDB for metadata) rather than in-memory or local filesystem, so that agents work correctly in AgentCore's ephemeral microVM sessions.

#### Acceptance Criteria
- `ProgressTracker` replaced with DynamoDB table (`CodeInsights-Progress`) with TTL
- `StorageManager` reads/writes analysis JSON to S3 bucket (`code-insights-analyses`)
- ATX conversation state persisted to S3 (`code-insights-atx-conversations/`)
- Design doc job state and outputs stored in S3 (`code-insights-design-docs/`)
- Transformation definitions stored in S3 (replacing EFS volume)
- All agents stateless within their microVM — state loaded on invocation, saved on completion
- Local dev mode: LocalStack or file-based fallback for DynamoDB/S3

**Validated by:** no design Property. Design section: "State Externalization" (the current → target mapping for all six state stores). The per-microVM statelessness obligation and the LocalStack fallback are stated only here. `structure.md`'s State Management Strategy records the current build's position explicitly: progress in memory, results and conversations on the local filesystem, no S3 or DynamoDB client anywhere in the backend.

# Service Contracts

API response formats and schemas for the Code Transformation Engine backend.

## Response Envelope Patterns

All list endpoints wrap their data in a named envelope key:

| Endpoint | Envelope Key | Example |
|----------|-------------|---------|
| `GET /api/analyses` | `analyses` | `{"analyses": [...]}` |
| `GET /api/analysis/{id}/file-stats` | `file_stats` | `{"file_stats": [...]}` |
| `GET /api/analysis/{id}/folder-structure` | `folder_structure` | `{"folder_structure": {...}}` |
| `GET /api/analysis/{id}/dependencies` | `dependencies` | `{"dependencies": [...]}` |
| `GET /api/analysis/{id}/dependency-graph` | `dependency_graph` | `{"dependency_graph": {"nodes": [...], "links": [...]}}` |
| `GET /api/analysis/{id}/upgrade-recommendations` | `upgrade_recommendations` | `{"upgrade_recommendations": [...]}` |
| `GET /api/analysis/{id}/diagrams` | `diagrams` | `{"diagrams": {"class_diagram": "...", ...}}` |
| `GET /api/transformations/definitions` | `definitions` | `{"definitions": [...]}` |
| `GET /api/analysis/{id}/summary` | _(top-level object)_ | `{"analysis_id": "...", "file_stats": [...], ...}` |
| `GET /api/analysis/{id}/documentation` | _(top-level)_ | `{"documentation": "...", "ai_enrichment_status": "..."}` |
| `POST /api/db-discovery/upload` | _(top-level)_ | `{"discovery_id": "...", "status": "processing", "message": "..."}` |
| `POST /api/db-discovery/github` | _(top-level)_ | body `{repo_url, branch, pat_token, subpath}` → same as upload |
| `GET /api/db-discovery/{id}/status` | _(top-level)_ | `{"discovery_id", "status", "progress", "current_step", "message"}` |
| `GET /api/db-discovery/{id}` | _(top-level object)_ | `{"discovery_id", "summary", "objects", "findings", "type_mappings", "application", ...}` |
| `GET /api/db-discovery/{id}/report.md` | _(text/markdown attachment)_ | Markdown assessment report |
| `GET /api/db-discoveries` | `discoveries` | `{"discoveries": [...]}` |
| `GET /api/db-discovery/rules` | `rules` | `{"rules": [...]}` |
| `DELETE /api/db-discovery/{id}` | _(top-level)_ | `{"detail": "Deleted"}` |

Database discovery reports are stored under `/app/temp/db_discoveries` (override with
`DB_DISCOVERY_STORAGE_PATH`), separately from code analyses, so they never appear in
`GET /api/analyses`. Report schema: `.kiro/specs/sqlserver-to-aurora-mysql/design.md`.

## Error Format

All error responses use a consistent JSON structure:

```json
{
  "detail": "Human-readable error message"
}
```

HTTP status codes:

| Status | Meaning |
|--------|---------|
| 400 | Invalid input (ValueError) |
| 401 | Missing or invalid JWT |
| 403 | Insufficient permissions |
| 404 | Resource not found (FileNotFoundError) |
| 429 | Rate limit exceeded (60 req/min per IP) |
| 500 | Internal server error |

## SSE Event Schema

Server-Sent Events use JSON payloads with a `type` discriminator:

```
data: {"type": "<event_type>", ...fields}
```

### Event Types

| Type | Fields | Description |
|------|--------|-------------|
| `init` | `conversation_id: string` | First event — identifies the session |
| `content` | `text: string` | Streamed text content (CLI output, AI text) |
| `progress` | `message: string, percentage: number` | Progress update (0–100) |
| `complete` | `conversation_id: string, status: string` | Stream finished successfully |
| `error` | `message: string` | Stream terminated with error |
| `info` | `text: string` | Informational message |
| `tool_use` | `tool: string, input: object` | Agent tool invocation (Strands) |
| `tool_result` | `tool: string, output: object` | Agent tool result |
| `cancelled` | `conversation_id: string` | Process was cancelled |

### SSE Wire Format

```
data: {"type": "init", "conversation_id": "github_20250115_143022"}

data: {"type": "content", "text": "Analyzing repository structure..."}

data: {"type": "progress", "message": "Parsing files", "percentage": 45}

data: {"type": "complete", "conversation_id": "github_20250115_143022", "status": "success"}
```

## Health Schema

All services expose a health endpoint:

```
GET /health
```

Response:

```json
{"status": "healthy"}
```

HTTP 200 indicates the service is ready to accept requests.

## Analysis ID Format

Analysis IDs follow the pattern:

```
{source}_{YYYYMMDD_HHMMSS}
```

- **source**: `upload` or `github` (code analysis); `dbupload` or `dbgithub` (database discovery,
  which appends `-2`, `-3`, … when two runs start in the same second)
- **timestamp**: UTC date and time of creation

Examples:
- `github_20250115_143022`
- `upload_20250210_091545`

Constraints:
- Alphanumeric characters, dashes (`-`), and underscores (`_`) only
- Validated by regex: `^[A-Za-z0-9_-]+$`
- Used as filesystem path component (no slashes or special chars)

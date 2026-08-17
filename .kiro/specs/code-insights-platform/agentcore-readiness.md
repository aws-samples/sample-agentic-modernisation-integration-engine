# AgentCore Readiness Assessment & Best Practices

## Spec Validation Summary

The generated Kiro spec (requirements.md, design.md, tasks.md) has been validated:
- **requirements.md** — 15 requirements, all passing format validation ✅
- **design.md** — Architecture, components, data models, error handling, testing, and 7 correctness properties ✅
- **tasks.md** — 65 tasks in 8 execution waves, dependency graph valid ✅

---

## AgentCore Deployment Gap Analysis

Amazon Bedrock AgentCore Runtime requires specific contract compliance for deploying agents. Below is a comparison of your current agents against AgentCore requirements.

### AgentCore Runtime Contract Requirements

| Requirement | Description |
|---|---|
| Platform | Must be `linux/arm64` |
| Endpoints | `/invocations` POST and `/ping` GET are **mandatory** |
| Port | Application must run on port **8080** |
| Container | ARM64 Docker image pushed to ECR |
| Session isolation | Each user session runs in a dedicated microVM |
| Credentials | Use execution role (not hardcoded keys) |

### Current State vs AgentCore Requirements

| Agent | Current Port | Has `/ping`? | Has `/invocations`? | ARM64? | AgentCore Ready? |
|-------|-------------|-------------|-------------------|--------|-----------------|
| Backend API (8000) | 8000 | ❌ (`/health`) | ❌ | ❌ (not specified) | ❌ |
| ATX Analysis (8004) | 8004 | ❌ (`/health`) | ❌ | ❌ | ❌ |
| ATX Transform (8005) | 8005 | ❌ (`/health`) | ❌ | ❌ | ❌ |
| Design Doc (8006) | 8006 | ❌ (`/health`) | ❌ | ❌ | ❌ |
| Kiro CLI (8007) | 8007 | ❌ (`/health`) | ❌ | ✅ (arm64 specified) | ❌ |
| Ant-to-Maven (8008) | 8008 | ❌ (`/health`) | ❌ | ❌ | ❌ |

### Key Gaps Identified

1. **Endpoint contract mismatch** — AgentCore requires `/ping` (GET) and `/invocations` (POST). Your agents use `/health` and service-specific REST endpoints.

2. **Port mismatch** — AgentCore requires port 8080. Your agents run on various ports (8000-8008).

3. **No `/invocations` dispatcher** — AgentCore sends all requests through a single `/invocations` POST endpoint with JSON payload. Your agents expose multiple REST routes.

4. **Platform architecture** — Only the Kiro CLI agent explicitly specifies `linux/arm64`. Others don't specify platform.

5. **Credential handling** — Current setup passes AWS credentials via environment variables in docker-compose. AgentCore provides credentials via microVM metadata service (MMDS).

6. **Session management** — AgentCore provides per-session microVM isolation. Your agents use shared in-memory state (ProgressTracker, StorageManager) that doesn't map to AgentCore's session model.

---

## Recommended Best Practices for AgentCore Deployment

### 1. Create AgentCore Adapter Layer

Add a thin adapter layer to each agent that translates between AgentCore's contract and your existing service endpoints:

```python
# agentcore_adapter.py — Add to each agent
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse

app = FastAPI()

@app.get("/ping")
async def ping():
    """AgentCore health check endpoint"""
    return {"status": "healthy"}

@app.post("/invocations")
async def invocations(request: Request):
    """AgentCore unified invocation endpoint.
    Routes to appropriate internal handler based on payload 'action' field."""
    payload = await request.json()
    action = payload.get("input", {}).get("action", "default")
    prompt = payload.get("input", {}).get("prompt", "")
    
    # Route to internal handlers based on action
    # Return results in AgentCore response format
    return {"output": {"message": result, "status": "success"}}
```

### 2. Port Standardization for AgentCore

Each agent deployed to AgentCore must listen on port **8080**. Create a build-time flag:

```dockerfile
# Dockerfile.agentcore — AgentCore variant
FROM --platform=linux/arm64 python:3.11-slim
# ... dependencies ...
ENV PORT=8080
EXPOSE 8080
CMD ["uvicorn", "agentcore_adapter:app", "--host", "0.0.0.0", "--port", "8080"]
```

### 3. ARM64 Architecture Mandate

All Dockerfiles must explicitly target `linux/arm64`:

```dockerfile
FROM --platform=linux/arm64 python:3.11-slim
```

Your CI/CD already builds with `DOCKER_PLATFORM: linux/arm64` — this is correct.

### 4. Credential Management via MMDS

Replace environment variable credential injection with AgentCore's microVM metadata service:

```python
# Instead of: os.environ.get("AWS_ACCESS_KEY_ID")
# AgentCore provides credentials automatically via execution role
# boto3 will automatically use the MMDS credentials in AgentCore microVMs

import boto3
# No explicit credentials needed — AgentCore handles this
bedrock_client = boto3.client("bedrock-runtime", region_name="us-east-1")
```

**Best practice**: Scope the execution role to minimum required permissions:
- `bedrock:InvokeModel` for specific model ARNs
- `bedrock:Retrieve` for Knowledge Base (design-doc-agent only)
- `s3:GetObject`/`PutObject` for storage (scoped to specific buckets)

### 5. Session Isolation Architecture

AgentCore runs each session in an isolated microVM. Adapt your agents:

| Current Pattern | AgentCore Pattern |
|---|---|
| Shared `ProgressTracker` (in-memory) | Per-session state within microVM filesystem |
| Shared `StorageManager` (file-based) | Persistent filesystem or external store (DynamoDB/S3) |
| Docker volumes for shared data | S3 or EFS for cross-session data |
| Background tasks (`BackgroundTasks`) | Process within the session; use S3 for persistence |

### 6. Streaming via AgentCore

AgentCore supports SSE streaming through `/invocations`. Update your SSE endpoints:

```python
@app.post("/invocations")
async def invocations(request: Request):
    payload = await request.json()
    
    if payload.get("input", {}).get("stream", False):
        # Return SSE stream
        return StreamingResponse(
            generate_sse_events(payload),
            media_type="text/event-stream"
        )
    else:
        # Return JSON response
        result = process_request(payload)
        return {"output": result}
```

### 7. Front with AgentCore Gateway

Deploy an AgentCore Gateway in front of your runtimes for:
- **Policy-based authorization** — control who can invoke which agent
- **Bedrock Guardrails** — apply content filtering at the gateway level
- **Request/response interceptors** — transform traffic via Lambda

```python
# Deploy gateway
client.create_gateway(
    gatewayName="code-insights-gateway",
    protocolType="HTTP"
)

# Add each agent as a target
client.create_target(
    gatewayId="gateway-id",
    targetName="doc-analysis-agent",
    targetConfiguration={
        "agentRuntimeTargetConfiguration": {
            "agentRuntimeArn": "arn:aws:bedrock-agentcore:...:runtime/doc-analysis"
        }
    }
)
```

### 8. Security Best Practices for AgentCore

Based on [AWS AgentCore security documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-security-best-practices.html):

| Practice | Implementation |
|---|---|
| **Least privilege IAM** | Create per-agent execution roles scoped to specific resources |
| **Confused deputy prevention** | Add `aws:SourceArn` and `aws:SourceAccount` conditions to trust policies |
| **VPC deployment** | Deploy runtimes in private subnets with VPC endpoints |
| **No hardcoded tokens** | Use AgentCore Identity for OAuth tokens to GitHub, etc. |
| **Run as non-root** | ✅ Already implemented in your Dockerfiles |
| **Restrict localhost access** | Limit agent code from accessing localhost (platform server) |
| **Enable CloudTrail** | Log all `InvokeAgentRuntime` calls for audit |
| **Use PrivateLink** | VPC endpoints for `bedrock-agentcore` and `bedrock-agentcore-control` |
| **Session timeout** | Set `idleRuntimeSessionTimeout` (e.g., 300s) and `maxLifetime` (e.g., 1800s) |
| **Deny user-id delegation** | Explicitly deny `InvokeAgentRuntimeForUser` unless needed |

### 9. Recommended Agent-to-AgentCore Migration Priority

| Priority | Agent | Reason |
|---|---|---|
| 1 | **Design Doc Agent** | Self-contained 5-stage pipeline, no shared state dependencies |
| 2 | **Kiro CLI Agent** | Already ARM64, stateless, simple request/response pattern |
| 3 | **Ant-to-Maven Agent** | Stateless conversion, fits `/invocations` pattern well |
| 4 | **ATX Analysis Agent** | Requires session persistence (conversations), more complex |
| 5 | **ATX Transform Agent** | Requires Docker-in-Docker and GitHub access, complex |
| 6 | **Backend API** | Hub service, many dependencies, deploy last |

### 10. MCP Server Deployment on AgentCore

AgentCore natively supports MCP servers. Your `CodeAssessorMCPServer` can be deployed directly:

- AgentCore expects MCP servers at `0.0.0.0:8000/mcp`
- Uses streamable-HTTP (stateless, session via `Mcp-Session-Id` header)
- Your current stdio-based MCP server needs conversion to HTTP transport

### 11. Observability

Enable AgentCore Observability for all deployed agents:
- CloudWatch Transaction Search for distributed tracing
- CloudWatch Logs for command auditing
- Correlate via request IDs across agents
- Set up metric filters for error rate anomalies

---

## Spec Update Recommendations

To make the spec AgentCore-ready, consider adding these requirements:

| # | New Requirement |
|---|---|
| FR-16 | AgentCore contract adapter (`/ping` GET, `/invocations` POST, port 8080) for all agents |
| FR-17 | AgentCore Gateway with policy-based routing to all agent runtimes |
| FR-18 | Per-agent IAM execution roles with minimum-privilege scoping |
| FR-19 | AgentCore Identity integration for GitHub PAT tokens (replace env var injection) |
| FR-20 | External state store (S3/DynamoDB) replacing in-memory ProgressTracker for AgentCore session model |

---

## Summary

Your current architecture is well-designed for Docker Compose / ECS Fargate deployment. Migrating to AgentCore requires:

1. **Contract compliance** — Add `/ping` + `/invocations` adapter layer (port 8080, ARM64)
2. **Credential migration** — Move from env vars to AgentCore execution roles / MMDS
3. **State externalization** — Move from in-memory/file to S3/DynamoDB for session isolation
4. **Gateway fronting** — Use AgentCore Gateway for unified auth, guardrails, and routing
5. **Observability** — Enable CloudWatch Transaction Search and audit logging

The good news: your security posture (non-root containers, input validation, rate limiting, SSRF protection) already aligns well with AgentCore's shared responsibility model. The Strands Agents framework you use is explicitly supported by AgentCore, making the migration path straightforward.

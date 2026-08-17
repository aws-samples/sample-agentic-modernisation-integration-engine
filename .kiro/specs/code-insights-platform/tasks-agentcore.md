# AgentCore Deployment Tasks (Deferred)

## Overview

These tasks add AWS Bedrock AgentCore Runtime deployment for serverless, session-isolated agent execution. They depend on the core platform (tasks 1-65) being complete first.

## Tasks

- [ ] 1. Create AgentCore adapter for Design Doc Agent: implement agentcore_adapter.py with /ping GET and /invocations POST (port 8080), route actions (create-job, get-job, rerun, regenerate) to existing pipeline handlers, support SSE streaming for long-running pipeline stages
- [ ] 2. Create AgentCore adapter for Kiro CLI Agent: implement agentcore_adapter.py with /ping and /invocations, route generate/generate-batch actions to existing SpecGenerator, SSE streaming for batch operations
- [ ] 3. Create AgentCore adapter for Ant-to-Maven Agent: implement agentcore_adapter.py with /ping and /invocations, route convert action to existing build.xml parser and pom.xml generator
- [ ] 4. Create AgentCore adapter for ATX Analysis Agent: implement agentcore_adapter.py with /ping and /invocations, route analyze/cancel/resume/browse actions, persist conversation state to S3 instead of local filesystem
- [ ] 5. Create AgentCore adapter for ATX Transform Agent: implement agentcore_adapter.py with /ping and /invocations, route transform/diff/create-pr actions, load transformation definitions from S3 instead of EFS volume
- [ ] 6. Create AgentCore adapter for Backend API: implement agentcore_adapter.py with /ping and /invocations, route analyze-upload/analyze-github/get-status/documentation/judge/file-analysis actions to existing handlers
- [ ] 7. Create Dockerfile.agentcore for each agent: FROM --platform=linux/arm64 python:3.11-slim, port 8080, non-root user, CMD uvicorn agentcore_adapter:app, health check on /ping
- [ ] 8. Implement external state store: replace ProgressTracker with DynamoDB table (CodeInsights-Progress, partition key: analysis_id, TTL attribute), replace StorageManager with S3 client (code-insights-analyses bucket), add local dev fallback using file-based storage when AGENTCORE_MODE=false
- [ ] 9. Implement S3-backed conversation storage for ATX agents: migrate /app/storage/{id}/ to S3 code-insights-atx/{id}/, implement async S3 read/write in RepositoryService and StorageService, maintain local cache within microVM session
- [ ] 10. Implement S3-backed design doc storage: migrate /app/storage/ to S3 code-insights-design-docs/{job_id}/, update PipelineOrchestrator to write outputs to S3, implement version management via S3 versioning
- [ ] 11. Create IAM execution roles via CloudFormation: deploy/stacks/12-agentcore-roles.yaml with 6 per-agent roles, least-privilege policies scoped to specific S3 buckets and Bedrock model ARNs, confused deputy prevention conditions (aws:SourceArn, aws:SourceAccount)
- [ ] 12. Deploy AgentCore Gateway: create gateway with HTTP protocol, register all 6 agent runtimes as targets, configure policy engine for caller-to-target authorization, attach Bedrock Guardrails (content filtering + PII detection)
- [ ] 13. Implement AgentCore Identity integration: replace GITHUB_TOKEN env var with AgentCore Identity OAuth credential store, implement GetWorkloadAccessToken calls in GitHubHandler and ATX Transform PR creation, remove credential env vars from docker-compose for AgentCore mode
- [ ] 14. Create AgentCore deployment script: deploy_agentcore.py using boto3 bedrock-agentcore-control client, create_agent_runtime for each agent with containerUri from ECR, networkConfiguration (VPC mode with private subnets), lifecycleConfiguration (idleTimeout: 300s, maxLifetime: 1800s)
- [ ] 15. Enable AgentCore observability: configure CloudWatch Transaction Search, add OpenTelemetry tracing to each adapter, set up CloudWatch Logs metric filters for error rates and latency, create CloudWatch dashboard for all agent runtimes
- [ ] 16. Create AgentCore VPC configuration: deploy/stacks/13-agentcore-vpc.yaml with private subnets, VPC endpoints for bedrock-agentcore, ecr.dkr, ecr.api, s3 (gateway), logs, dynamodb, security groups with least-privilege outbound rules
- [ ] 17. Update GitLab CI/CD for AgentCore deployment: add deploy-agentcore stage after deploy-services, build Dockerfile.agentcore variants, push to ECR, invoke create_agent_runtime or update_agent_runtime, verify with invoke_agent_runtime health check
- [ ] 18. Implement gateway interceptor Lambda: create Lambda function for request/response audit logging, log caller identity, action, timestamp, response status to CloudWatch Logs, attach to AgentCore Gateway as interceptor

## Task Dependency Graph

```json
{
  "waves": [
    [1, 2, 3, 7, 8, 11, 16],
    [4, 5, 6, 9, 10, 13],
    [12, 14, 15, 17, 18]
  ]
}
```

## Notes

- Wave 1 (tasks 1-3, 7-8, 11, 16) can begin once the core platform is running
- Wave 2 (tasks 4-6, 9-10, 13) depends on state externalization (task 8) being complete
- Wave 3 (tasks 12, 14-15, 17-18) depends on all adapters and infrastructure being ready
- Relates to Requirements 16-20 in requirements.md
- See agentcore-readiness.md for gap analysis and best practices

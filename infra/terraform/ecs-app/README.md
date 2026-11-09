# AMIE on ECS Fargate behind CloudFront

Deploys the app to AWS: the four containers `docker compose` runs, an Aurora MySQL sandbox in
place of the local `mysql` container, and EFS in place of the named volumes. Users reach it at
`https://<id>.cloudfront.net` and sign in with one fixed username and password.

> Demo stack. No deletion protection, no final snapshots, one task, one NAT gateway. It bills
> while it runs (roughly $90–130/month); `terraform destroy` removes everything.

## What it creates

```
Browser ──HTTPS──▶ CloudFront ──VPC origin──▶ internal ALB ──▶ ECS task (private subnets)
                  (*.cloudfront.net cert)      (port 80)        ├─ frontend  nginx :3000
                                                                 ├─ backend   FastAPI :8000 ──▶ Bedrock
                                                                 ├─ atx-analysis-agent :8004
                                                                 └─ atx-transform-agent :8005
                                                                      │
                                       EFS (/app/temp, /app/storage) ◀┤
                                       Aurora MySQL 3 Serverless v2  ◀┤  conversion sandbox + validation target
                                       RDS SQL Server (../rds-sqlserver-test) ◀┘  source for behaviour tests
```

| Piece | Detail |
|---|---|
| Network | Two private subnets and a NAT gateway added to the **default VPC** (or `vpc_id`), the same VPC as the test SQL Server, so the task reaches it privately. Nothing in the app has a public IP. |
| Edge | CloudFront with its default certificate, caching off except `/assets/*`, all headers forwarded. The ALB accepts only CloudFront's origin-facing prefix list. |
| Compute | One Fargate task (ARM64, 4 vCPU / 10 GB) running all four containers. They share the task network, so nginx proxies to `127.0.0.1`. |
| Sign-in | The app's local auth mode with a fixed user: `LOCAL_AUTH_USERNAME` / `LOCAL_AUTH_PASSWORD`. nginx checks the same JWT before passing `/atx/` and `/atx-transform/` to the ATX agents, which have no auth of their own. |
| Data | EFS access points for each former Docker volume. Aurora MySQL 3 Serverless v2 (0.5–4 ACU) with `log_bin_trust_function_creators=1`, matching the local sandbox. |
| Secrets | Secrets Manager: the app password and JWT key (this stack), Aurora's RDS-managed master secret, and the SQL Server's RDS-managed master secret. Injected as container secrets; never in task-definition plain text. |
| IAM | Task role: Bedrock invoke on foundation models and inference profiles, `AWSTransformCustomFullAccess` for the ATX CLI, ECS Exec. All four containers share it. |
| Test SQL Server | Looked up by identifier (`amie-wwi-test`); this stack adds one ingress rule to its security group for the task. Set `sqlserver_enabled = false` if it is not deployed. |

## Deploy

Run with credentials that can create VPC, ECS, RDS, EFS, CloudFront, IAM and Secrets Manager
resources. If `.env` exports the Bedrock-only `test` user's keys into your shell, prefix commands
with `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY`.

```bash
cd infra/terraform/ecs-app
cp terraform.tfvars.example terraform.tfvars

terraform init
# 1. Repositories first, so there is somewhere to push.
terraform apply -target=aws_ecr_repository.this
./build-push.sh
# 2. Everything else (~20 min: Aurora and CloudFront are the slow parts).
terraform apply

terraform output app_url
terraform output -raw app_password
```

After a code change: `./build-push.sh --deploy`. The service stops the old task before starting
the new one, so expect a minute or two of downtime.

## Operate

```bash
aws logs tail "$(terraform output -raw log_group)" --follow
aws ecs execute-command --cluster amie-app --task <task-id> --container backend --interactive --command sh
```

## Limits

- **SSE through CloudFront.** CloudFront closes an origin connection that sends nothing for
  `origin_read_timeout` seconds (60 by default; up to 180 needs a CloudFront quota increase). AI and
  ATX streams that go quiet for longer are cut off.
- **One task.** State is JSON files on EFS written by one process. Running two tasks would let them
  overwrite each other's files.
- **Shared task role.** The four containers share one IAM role; separate services would allow a
  role each.
- **CORS** is set to the CloudFront URL. The SPA and API share that origin, so it only matters for
  other callers.
- `use1-az3` is skipped: CloudFront VPC origins do not support it.

## Tear down

```bash
terraform destroy
```

This also removes the ingress rule it added to the test SQL Server's security group; the SQL Server
itself belongs to `../rds-sqlserver-test`.

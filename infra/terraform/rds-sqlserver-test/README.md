# Test RDS for SQL Server (WideWorldImporters)

A **test-only** Amazon RDS for SQL Server instance for the AMIE validation harness: the source side of
"SQL Server → Aurora MySQL". It is never created by the application or CI; you run it.

Creates: an RDS SQL Server instance (Express by default), a security group open on 1433 to
`allowed_cidr` only, an RDS-managed master password in AWS Secrets Manager, an encrypted S3 bucket,
and an IAM role plus option group for native backup/restore (`SQLSERVER_BACKUP_RESTORE`).
Everything is tagged `Purpose=amie-test`. There is no deletion protection and no final snapshot.

## Cost

Billed while it exists: the instance (`db.t3.small`, licence included), 20 GB gp3 storage, one day of
backups, the S3 object (~120 MB), and the secret. Run `terraform destroy` when you are done.

## Use

```bash
cd infra/terraform/rds-sqlserver-test
terraform init
terraform apply -var "allowed_cidr=$(curl -s https://checkip.amazonaws.com)/32"
./restore-wwi.sh                 # ~10–20 min; WRITE_ENV=1 ./restore-wwi.sh writes the .env lines
cd ../../.. && docker compose up -d --force-recreate backend
```

Then open **Database Discovery → (a discovery) → Validation**. The status panel shows whether the
source and target are reachable.

Your IP can change (VPN, Wi-Fi). If the status shows the source unreachable, re-apply with the new
`allowed_cidr`. If the port is blocked by your network, set `port` (the instance restarts briefly).

## Variables

| Variable | Default | Notes |
|---|---|---|
| `allowed_cidr` | — (required) | Your IP as `/32`. `0.0.0.0/0` is rejected. |
| `region` | `us-east-1` | |
| `engine` | `sqlserver-ex` | Use `sqlserver-se` if an Express limit blocks the restore (10 GB data limit, 1 socket). |
| `instance_class` | `db.t3.small` | |
| `port` | `1433` | Many corporate and guest networks block outbound 1433. Check with `nc -z portquiz.net 1433`; if blocked, use e.g. `50000`. |
| `publicly_accessible` | `true` | Needed for a laptop to connect; access stays limited to `allowed_cidr`. |
| `vpc_id`, `subnet_ids` | default VPC | Set both for a private network reached over VPN. |

## Tear down

```bash
terraform destroy -var "allowed_cidr=0.0.0.1/32"
```

The bucket has `force_destroy = true`, so the uploaded `.bak` is deleted with it.

#!/usr/bin/env bash
# Load WideWorldImporters into the test RDS SQL Server created by this Terraform folder.
#
#   1. downloads WideWorldImporters-Full.bak (MIT, Microsoft sql-server-samples release)
#   2. uploads it to the Terraform-created backup bucket
#   3. runs msdb.dbo.rds_restore_database and waits for it to finish
#   4. prints the VALIDATION_SQLSERVER_* lines for the repo .env (WRITE_ENV=1 appends them)
#
# Needs: terraform outputs (run `terraform apply` first), aws CLI, curl, uv.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../.." && pwd)"
BAK_URL="https://github.com/Microsoft/sql-server-samples/releases/download/wide-world-importers-v1.0/WideWorldImporters-Full.bak"
DB_NAME="${DB_NAME:-WideWorldImporters}"
CACHE="${CACHE:-$HOME/.cache/amie}"

cd "$HERE"
ENDPOINT="$(terraform output -raw endpoint)"
PORT="$(terraform output -raw port)"
USERNAME="$(terraform output -raw master_username)"
SECRET_ARN="$(terraform output -raw master_secret_arn)"
BUCKET="$(terraform output -raw backup_bucket)"
REGION="$(terraform output -raw region 2>/dev/null || echo "${AWS_REGION:-us-east-1}")"

mkdir -p "$CACHE"
if [ ! -s "$CACHE/WideWorldImporters-Full.bak" ]; then
  echo "Downloading WideWorldImporters-Full.bak (~120 MB)..."
  curl -fL --retry 3 -o "$CACHE/WideWorldImporters-Full.bak" "$BAK_URL"
fi

echo "Uploading to s3://$BUCKET/ ..."
aws s3 cp --region "$REGION" "$CACHE/WideWorldImporters-Full.bak" "s3://$BUCKET/WideWorldImporters-Full.bak" --only-show-errors

PASSWORD="$(aws secretsmanager get-secret-value --region "$REGION" --secret-id "$SECRET_ARN" \
  --query SecretString --output text | python3 -c 'import json,sys;print(json.load(sys.stdin)["password"])')"

echo "Restoring $DB_NAME on $ENDPOINT (this takes several minutes)..."
SQLSERVER_HOST="$ENDPOINT" SQLSERVER_PORT="$PORT" SQLSERVER_USER="$USERNAME" SQLSERVER_PASSWORD="$PASSWORD" \
DB_NAME="$DB_NAME" S3_ARN="arn:aws:s3:::$BUCKET/WideWorldImporters-Full.bak" \
uv run --quiet --no-project --with "pymssql>=2.3,<3" python - <<'PY'
import os, sys, time
import pymssql

conn = pymssql.connect(server=os.environ["SQLSERVER_HOST"], port=os.environ["SQLSERVER_PORT"],
                       user=os.environ["SQLSERVER_USER"], password=os.environ["SQLSERVER_PASSWORD"],
                       database="master", autocommit=True, login_timeout=30)
cur = conn.cursor()
db = os.environ["DB_NAME"]
cur.execute("SELECT COUNT(*) FROM sys.databases WHERE name = %s", (db,))
if cur.fetchone()[0]:
    print(f"{db} already exists; skipping restore.")
    sys.exit(0)
cur.execute("EXEC msdb.dbo.rds_restore_database @restore_db_name = %s, @s3_arn_to_restore_from = %s",
            (db, os.environ["S3_ARN"]))
while True:
    time.sleep(20)
    cur.execute("EXEC msdb.dbo.rds_task_status @db_name = %s", (db,))
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    task = rows[-1] if rows else {}
    status, pct = task.get("lifecycle"), task.get("% complete")
    print(f"  {status} {pct}%", flush=True)
    if status == "SUCCESS":
        break
    if status in ("ERROR", "CANCELLED"):
        print(task.get("task_info", ""), file=sys.stderr)
        sys.exit(1)
PY

ENV_LINES="VALIDATION_SQLSERVER_HOST=$ENDPOINT
VALIDATION_SQLSERVER_PORT=$PORT
VALIDATION_SQLSERVER_USER=$USERNAME
VALIDATION_SQLSERVER_PASSWORD=$PASSWORD
VALIDATION_SQLSERVER_DATABASE=$DB_NAME"

if [ "${WRITE_ENV:-0}" = "1" ]; then
  ENV_FILE="$REPO_ROOT/.env"
  tmp="$(mktemp)"
  grep -vE '^VALIDATION_SQLSERVER_(HOST|PORT|USER|PASSWORD|DATABASE)=' "$ENV_FILE" > "$tmp" || true
  printf '\n%s\n' "$ENV_LINES" >> "$tmp"
  mv "$tmp" "$ENV_FILE"
  echo "Wrote VALIDATION_SQLSERVER_* to $ENV_FILE. Recreate the backend: docker compose up -d --force-recreate backend"
else
  echo
  echo "Add these to the repo .env (or re-run with WRITE_ENV=1), then: docker compose up -d --force-recreate backend"
  echo "$ENV_LINES" | sed 's/^VALIDATION_SQLSERVER_PASSWORD=.*/VALIDATION_SQLSERVER_PASSWORD=<from the master secret>/'
fi

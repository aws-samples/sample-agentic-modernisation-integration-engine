#!/usr/bin/env bash
# Builds the four ARM64 images, pushes them to the ECR repositories this stack created, and
# (with --deploy) restarts the ECS service so it pulls them.
#
#   ./build-push.sh            # build + push
#   ./build-push.sh --deploy   # build + push + force a new ECS deployment
#
# Run `terraform apply` first: the repository URLs come from `terraform output`.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../../.." && pwd)"
tag="${IMAGE_TAG:-latest}"

# Read settings with `terraform console`, which works before the first full apply (outputs are
# only written by one), then look the repositories up in ECR.
tf_eval() { echo "$1" | terraform -chdir="$here" console | tr -d '"'; }
region="$(tf_eval var.region)"
name="$(tf_eval var.name)"
account="$(aws sts get-caller-identity --query Account --output text)"
registry="$account.dkr.ecr.$region.amazonaws.com"

aws ecr get-login-password --region "$region" | docker login --username AWS --password-stdin "$registry"

# backend/requirements.txt is what the backend Dockerfile installs; keep it in step with uv.lock.
(cd "$repo_root/backend" && make export >/dev/null)

for image in frontend backend atx-analysis-agent atx-transform-agent; do
  url="$(aws ecr describe-repositories --region "$region" --repository-names "$name/$image" \
    --query 'repositories[0].repositoryUri' --output text)"
  echo "==> $image -> $url:$tag"
  docker buildx build --platform linux/arm64 --provenance=false \
    -t "$url:$tag" --push "$repo_root/$image"
done

if [[ "${1:-}" == "--deploy" ]]; then
  cluster="$name"
  service="$name"
  aws ecs update-service --region "$region" --cluster "$cluster" --service "$service" \
    --force-new-deployment --query 'service.deployments[0].rolloutState' --output text
  echo "Deployment started. Watch: aws ecs describe-services --cluster $cluster --services $service"
fi

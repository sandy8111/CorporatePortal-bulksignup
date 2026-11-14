#!/usr/bin/env bash
# Build the assistant image for AgentCore Runtime (linux/arm64) and push it to ECR.
# Usage: AWS_REGION=ap-southeast-1 agent/build_and_push.sh [tag]
set -euo pipefail
cd "$(dirname "$0")"
REGION="${AWS_REGION:?set AWS_REGION}"
TAG="${1:-$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M)}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REPO="corpone-assistant"
URI="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$REPO:$TAG"
aws ecr describe-repositories --repository-names "$REPO" --region "$REGION" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "$REPO" --region "$REGION" \
       --image-scanning-configuration scanOnPush=true --encryption-configuration encryptionType=KMS >/dev/null
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
docker buildx build --platform linux/arm64 -t "$URI" --push .
echo "AGENT_IMAGE_URI=$URI"

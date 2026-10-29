#!/usr/bin/env bash

# call using:
# ./deploy.sh prod (for production-ready stack)


# Exit immediately if any command fails
set -e

# --- CONFIGURATION ---
ENVIRONMENT="${1:-dev}" # Defaults to 'dev' (Run './deploy.sh prod' for production)
APP_NAME="SimpleObjectDetector"
STACK_NAME="${APP_NAME}-${ENVIRONMENT}"
REGION="us-east-1" # Set your preferred target AWS region

# Automatically create a lowercased version of the app name for S3 mapping
# (Works natively in Bash/Zsh to convert strings to lowercase)
LOW_APP_NAME=$(echo "$APP_NAME" | tr '[:upper:]' '[:lower:]')

echo "===================================================="
echo " Starting Full-Stack Deployment for [${ENVIRONMENT}]"
echo "===================================================="

# 0. Clear local Build cache to prevent stale artifacts
echo "👉 Clearing local cache..."
rm -rf .aws-sam/

# 1. Validate SAM Template Syntax
echo "👉 Validating SAM template..."
sam validate

# 2. Build the Application Architecture
echo "👉 Building serverless architecture..."
# SAM only finds Docker at /var/run/docker.sock or $DOCKER_HOST. Docker Desktop on macOS
# may only expose its socket elsewhere, so fall back to the active docker context.
if [ -z "${DOCKER_HOST:-}" ] && [ ! -S /var/run/docker.sock ]; then
    DOCKER_HOST=$(docker context inspect --format '{{.Endpoints.docker.Host}}' 2>/dev/null || true)
    export DOCKER_HOST
fi
# --use-buildkit: the legacy builder fails with "failed to export image ... not found" when
# Docker Desktop's containerd image store builds the x86_64 image on Apple Silicon
sam build --use-container --use-buildkit

# 3. Deploy Infrastructure to AWS
echo "👉 Checking existing stack status..."

# Fetch the current stack status (suppressing errors if the stack doesn't exist yet)
STACK_STATUS=$(aws cloudformation describe-stacks \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}" \
    --query "Stacks[0].StackStatus" \
    --output text 2>/dev/null || echo "NOT_FOUND")

echo "   Current stack status: ${STACK_STATUS}"

# If the stack is in a blocked rollback state, delete it automatically
if [ "${STACK_STATUS}" == "ROLLBACK_COMPLETE" ] || [ "${STACK_STATUS}" == "ROLLBACK_FAILED" ]; then
    echo "⚠️ Stack is stuck in an un-updatable state (${STACK_STATUS}). Purging stack..."
    aws cloudformation delete-stack --stack-name "${STACK_NAME}" --region "${REGION}"
    
    echo "⏳ Waiting for stack deletion to complete (this may take a minute)..."
    aws cloudformation wait stack-delete-complete --stack-name "${STACK_NAME}" --region "${REGION}"
    echo "✅ Broken stack purged successfully. Proceeding with clean deployment."
fi

echo "👉 Deploying infrastructure to AWS..."
# ECR repository for the container image: use the one in a local samconfig.toml if set,
# otherwise let SAM create and manage one (CI runners and fresh clones have no samconfig.toml)
IMAGE_REPO_ARGS=()
if ! grep -qs 'image_repositories' samconfig.toml; then
    IMAGE_REPO_ARGS=(--resolve-image-repos)
fi

sam deploy \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}" \
    --resolve-s3 \
    "${IMAGE_REPO_ARGS[@]}" \
    --no-fail-on-empty-changeset \
    --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND \
    --parameter-overrides "LowerAppName=${LOW_APP_NAME} EnvironmentName=${ENVIRONMENT}"

echo "🔍 Validating stack state synchronously..."
# Fetch the immediate state post-deployment execution block
FINAL_STATUS=$(aws cloudformation describe-stacks --stack-name "${STACK_NAME}" --region "${REGION}" --query "Stacks[0].StackStatus" --output text 2>/dev/null || echo "FAILED")

# Catch if the stack immediately rolled back or hit a terminal limit
if [[ "$FINAL_STATUS" == *"ROLLBACK"* ]] || [[ "$FINAL_STATUS" == *"FAILED"* ]]; then
    echo "❌ Deployment failed inside AWS CloudFormation. Printing root errors:"
    aws cloudformation describe-stack-events --stack-name "${STACK_NAME}" --region "${REGION}" \
        --query "StackEvents[?ResourceStatus=='CREATE_FAILED' || ResourceStatus=='UPDATE_FAILED'].{Resource:LogicalResourceId, Reason:ResourceStatusReason}" --output table
    exit 1
fi

# 4. Fetch the generated S3 bucket, Website URL, and API Gateway URL
echo "👉 Fetching CloudFormation Outputs..."
BUCKET_NAME=$(aws cloudformation describe-stacks \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}" \
    --query "Stacks[0].Outputs[?OutputKey=='S3BucketName'].OutputValue" \
    --output text)

WEBSITE_URL=$(aws cloudformation describe-stacks \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}" \
    --query "Stacks[0].Outputs[?OutputKey=='WebsiteURL'].OutputValue" \
    --output text)

API_URL=$(aws cloudformation describe-stacks \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}" \
    --query "Stacks[0].Outputs[?OutputKey=='HttpApiUrl'].OutputValue" \
    --output text)

DISTRIBUTION_ID=$(aws cloudformation describe-stacks \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}" \
    --query "Stacks[0].Outputs[?OutputKey=='DistributionId'].OutputValue" \
    --output text)

# Safeguard check to ensure variables are not empty
if [ -z "${BUCKET_NAME}" ] || [ "${BUCKET_NAME}" == "None" ]; then
    echo "❌ Error: Could not find an OutputKey named 'S3BucketName' in stack ${STACK_NAME}."
    exit 1
fi

# 5. Inject Configuration dynamically into config.js as global variables
echo "👉 Generating frontend configuration into config.js..."
cat << EOF > ./frontend/config.js
// Generated automatically by deploy.sh on $(date)
window.ENV_CONFIG = {
    API_URL: "${API_URL}",
    ENVIRONMENT: "${ENVIRONMENT}"
};
EOF

# 6. Upload only specific frontend assets to the target S3 bucket
echo "👉 Uploading selected frontend assets to S3 bucket ($BUCKET_NAME)..."
FILES_TO_UPLOAD=("index.html" "main.js" "config.js")

for FILE in "${FILES_TO_UPLOAD[@]}"; do
    if [ -f "./frontend/${FILE}" ]; then
        echo "   Uploading ${FILE}..."
        aws s3 cp "./frontend/${FILE}" "s3://${BUCKET_NAME}/${FILE}"
    else
        echo "   ⚠️ Warning: ./frontend/${FILE} not found. Skipping."
    fi
done

# 7. Clear CloudFront's cache so visitors get the new files immediately
echo "👉 Invalidating CloudFront cache (${DISTRIBUTION_ID})..."
aws cloudfront create-invalidation \
    --distribution-id "${DISTRIBUTION_ID}" \
    --paths "/*" \
    --query "Invalidation.Id" \
    --output text

echo "===================================================="
echo " ✅ Deployment Completed! [${ENVIRONMENT}] website is live."
echo " 🌐 Website URL:  ${WEBSITE_URL}"
echo " ⚡ API Endpoint: ${API_URL}"
echo "===================================================="

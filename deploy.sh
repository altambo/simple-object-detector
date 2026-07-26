#!/usr/bin/env bash

# call using:
# ./deploy.sh prod (for production-ready stack)


# Exit immediately if any command fails
set -e

# --- CONFIGURATION ---
ENVIRONMENT="${1:-dev}" # Defaults to 'dev' (Run './deploy.sh prod' for production)
APP_NAME="OmniDetection-app"
STACK_NAME="${APP_NAME}-${ENVIRONMENT}"
REGION="us-east-1" # Set your preferred target AWS region

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
sam build --use-container

# 3. Deploy Infrastructure to AWS
echo "👉 Deploying infrastructure to AWS..."
sam deploy \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}" \
    --resolve-s3 \
    --no-fail-on-empty-changeset \
    --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND

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

echo "===================================================="
echo " ✅ Deployment Completed! [${ENVIRONMENT}] website is live."
echo " 🌐 Website URL: ${WEBSITE_URL}"
echo " ⚡ API Endpoint: ${API_URL}"
echo "===================================================="

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
sam build --use-container

# --- PRE-DEPLOYMENT S3 CLEANUP SANITIZATION ---
# Calculate the exact bucket name CloudFormation will attempt to build
EXPECTED_BUCKET_NAME="${LOW_APP_NAME}-${ENVIRONMENT}-frontend-bucket"


# echo "👉 Checking if S3 bucket '${EXPECTED_BUCKET_NAME}' already exists..."
# # Query AWS to see if the bucket exists and if you have access to it
# if aws s3api head-bucket --bucket "${EXPECTED_BUCKET_NAME}" 2>/dev/null; then
#     echo "⚠️ Conflict Warning: S3 bucket '${EXPECTED_BUCKET_NAME}' already exists."
#     echo "🗑️ Purging all contents and deleting the bucket to ensure a clean state..."
    
#     # 1. Force-remove all object versions, files, folders, and markers inside the bucket
#     aws s3 rm "s3://${EXPECTED_BUCKET_NAME}" --recursive
    
#     # If your bucket has versioning turned on, we also clean up hidden object versions to prevent deletion blocks
#     echo "   Clearing out object versions and deletion markers..."
#     aws s3api delete-objects --bucket "${EXPECTED_BUCKET_NAME}" \
#         --delete "$(aws s3api list-object-versions --bucket "${EXPECTED_BUCKET_NAME}" \
#         --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' --output json 2>/dev/null || echo '{"Objects":[]}')" 2>/dev/null || true
        
#     aws s3api delete-objects --bucket "${EXPECTED_BUCKET_NAME}" \
#         --delete "$(aws s3api list-object-versions --bucket "${EXPECTED_BUCKET_NAME}" \
#         --query '{Objects: DeleteMarkers[].{Key:Key,VersionId:VersionId}}' --output json 2>/dev/null || echo '{"Objects":[]}')" 2>/dev/null || true

#     # 2. Delete the now-empty bucket structure from the AWS account
#     echo "   Removing root bucket architecture..."
#     aws s3api delete-bucket --bucket "${EXPECTED_BUCKET_NAME}" --region "${REGION}"
#     echo "✅ Existing S3 bucket successfully destroyed. Proceeding to deployment."
# else
#     echo "   No conflicting S3 bucket found. Safe to proceed."
# fi


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
sam deploy \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}" \
    --resolve-s3 \
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

# Safeguard check to ensure variables are not empty
if [ -z "${BUCKET_NAME}" ] || [ "${BUCKET_NAME}" == "None" ]; then
    echo "❌ Error: Could not find an OutputKey named 'S3BucketName' in stack ${STACK_NAME}."
    exit 1
fi

# Clean up trailing slashes on WebsiteURL to prevent double-slashes in the output string
CLEAN_WEBSITE_URL=$(echo "$WEBSITE_URL" | sed 's/\/$//')
INDEX_HTML_URL="${CLEAN_WEBSITE_URL}/index.html"

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
echo "👉 Initializing bucket directory structure..."
sleep 10
aws s3api put-object --bucket "${BUCKET_NAME}" --key "data/"

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
echo " 🌐 Website Root URL: ${WEBSITE_URL}"
echo " 📄 Direct Index URL: ${INDEX_HTML_URL}" # ✅ Prints your custom direct link
echo " ⚡ API Endpoint:     ${API_URL}"
echo "===================================================="

#!/usr/bin/env bash
# Build the SOD_processImage container and test it locally before deploying.
# Used by CI (.github/workflows/ci.yml) and runnable on your machine:
#
#   ./scripts/test_container.sh            # build, then test
#   SKIP_BUILD=1 ./scripts/test_container.sh my-image:tag   # test an existing image
#
# No AWS account is needed. The checks are:
#   1. the image stays small (cold-start regression guard)
#   2. the handler module imports and the model detects objects inside the image
#   3. the Lambda runtime (via the emulator built into the AWS base image) invokes the handler

set -euo pipefail

IMAGE="${1:-sod-process-image:test}"
MAX_IMAGE_MB="${MAX_IMAGE_MB:-1000}"
RIE_PORT="${RIE_PORT:-9000}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONTAINER_NAME="sod-container-test-$$"

# Placeholder values; the checks below never reach AWS
ENV_ARGS=(
    -e AWS_DEFAULT_REGION=us-east-1
    -e AWS_ACCESS_KEY_ID=test
    -e AWS_SECRET_ACCESS_KEY=test
    -e DATABASE_NAME=container-test-table
    -e TARGET_BUCKET=container-test-bucket
)

cleanup() { docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

if [ "$(wc -c < "$ROOT/backend-container/yolov8m.onnx")" -lt 1000000 ]; then
    echo "❌ backend-container/yolov8m.onnx is a Git LFS pointer. Run: git lfs pull"
    exit 1
fi

if [ "${SKIP_BUILD:-0}" != "1" ]; then
    echo "👉 Building ${IMAGE}..."
    docker build --platform linux/amd64 --provenance false -t "$IMAGE" "$ROOT/backend-container"
fi

echo "👉 1. Checking image size..."
SIZE_MB=$(( $(docker image inspect -f '{{.Size}}' "$IMAGE") / 1024 / 1024 ))
echo "   Image size: ${SIZE_MB} MB (limit ${MAX_IMAGE_MB} MB)"
if [ "$SIZE_MB" -gt "$MAX_IMAGE_MB" ]; then
    echo "❌ Image is larger than ${MAX_IMAGE_MB} MB. Did a heavy dependency (e.g. torch) get added?"
    exit 1
fi

echo "👉 2. Running the detector inside the image..."
docker run --rm --platform linux/amd64 "${ENV_ARGS[@]}" \
    -e AWS_LAMBDA_FUNCTION_NAME=container-test \
    -v "$ROOT/tests/fixtures:/fixtures:ro" \
    --entrypoint python "$IMAGE" -c '
import sys
import app
from detector import load_image, summarize
dets = app.MODEL.detect(load_image("/fixtures/bus.jpg"))
labels = [d.label for d in dets]
print("   " + summarize(dets))
assert "bus" in labels and labels.count("person") >= 3, "unexpected detections"
heavy = [m for m in ("torch", "ultralytics", "cv2") if m in sys.modules]
assert not heavy, f"heavy modules imported: {heavy}"
'

echo "👉 3. Invoking the handler through the Lambda runtime emulator..."
docker run -d --name "$CONTAINER_NAME" --platform linux/amd64 "${ENV_ARGS[@]}" \
    -p "${RIE_PORT}:8080" "$IMAGE" >/dev/null

INVOKE_URL="http://localhost:${RIE_PORT}/2015-03-31/functions/function/invocations"
# An unknown inputType is rejected before any AWS call, so this exercises the
# real Lambda entrypoint (cold start, model load, handler) without AWS access.
EVENT='{"Records": [{"body": "{\"inputType\": \"CONTAINER_TEST\", \"imageId\": \"container-test\"}"}]}'
RESPONSE=""
for _ in $(seq 1 60); do
    if RESPONSE=$(curl -sf -X POST "$INVOKE_URL" -d "$EVENT" 2>/dev/null); then break; fi
    sleep 1
done
echo "   Response: ${RESPONSE}"
if [[ "$RESPONSE" != *'"statusCode": 400'* ]]; then
    echo "❌ Unexpected response from the handler. Container logs:"
    docker logs "$CONTAINER_NAME" | tail -30
    exit 1
fi

echo "✅ Container tests passed."

"""
Shared test fixtures. AWS is mocked with moto, so no real account is touched.

The Lambda modules create their boto3 clients at import time, so the mock and
the fake resources must exist before those modules are imported. The `aws`
fixture does that once per session and the module fixtures import lazily.
"""
import importlib
import os
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
MODEL_PATH = REPO_ROOT / "backend-container" / "yolov8m.onnx"

BUCKET = "test-uploads-bucket"
TABLE = "test-job-status-table"
QUEUE = "test-queue"

# Without `git lfs pull`, the model file is a ~130 byte pointer, not the weights
MODEL_AVAILABLE = MODEL_PATH.exists() and MODEL_PATH.stat().st_size > 1_000_000
requires_model = pytest.mark.skipif(
    not MODEL_AVAILABLE, reason="yolov8m.onnx is a Git LFS pointer; run `git lfs pull`"
)


@pytest.fixture(scope="session")
def aws():
    env = {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": "us-east-1",
        # Makes the handlers skip load_dotenv(), so a local .env can't leak into tests
        "AWS_LAMBDA_FUNCTION_NAME": "pytest",
    }
    old_env = {k: os.environ.get(k) for k in env}
    os.environ.update(env)

    with mock_aws():
        boto3.client("s3").create_bucket(Bucket=BUCKET)
        boto3.client("dynamodb").create_table(
            TableName=TABLE,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[{"AttributeName": "imageId", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "imageId", "KeyType": "HASH"}],
        )
        queue_url = boto3.client("sqs").create_queue(QueueName=QUEUE)["QueueUrl"]
        os.environ.update({"TARGET_BUCKET": BUCKET, "DATABASE_NAME": TABLE, "QUEUE_URL": queue_url})
        yield {"bucket": BUCKET, "table": TABLE, "queue_url": queue_url}

    for k, v in old_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _import(name):
    return importlib.import_module(name)


@pytest.fixture(scope="session")
def get_image_url(aws):
    return _import("get_image_url")


@pytest.fixture(scope="session")
def check_image_status(aws):
    return _import("check_image_status")


@pytest.fixture(scope="session")
def app(aws):
    if not MODEL_AVAILABLE:
        pytest.skip("yolov8m.onnx is a Git LFS pointer; run `git lfs pull`")
    return _import("app")


@pytest.fixture(scope="session")
def bus_image_path():
    return FIXTURES / "bus.jpg"


def get_job(image_id):
    return boto3.resource("dynamodb").Table(TABLE).get_item(Key={"imageId": image_id}).get("Item")

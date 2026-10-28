"""Tests for the API-facing Lambdas: get_image_url (POST /) and check_image_status (GET /{imageId})."""
import base64
import datetime
import json
import uuid

import boto3
import pytest

from conftest import get_job


def _post(body):
    return {"body": json.dumps(body), "isBase64Encoded": False}


def _receive_all(queue_url):
    resp = boto3.client("sqs").receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10)
    return [json.loads(m["Body"]) for m in resp.get("Messages", [])]


# --- get_image_url ------------------------------------------------------------

def test_local_upload_stores_image_records_job_and_enqueues(get_image_url, aws, bus_image_path):
    image_b64 = base64.b64encode(bus_image_path.read_bytes()).decode()
    resp = get_image_url.lambda_handler(_post({"type": "local", "image": image_b64}), None)

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    image_id = body["imageId"]
    assert set(body) == {"imageId"}  # the uploaded image is not echoed back

    today = datetime.datetime.now()
    key = f"data/{today:%Y}/{today:%m}/{image_id}.jpg"
    obj = boto3.client("s3").get_object(Bucket=aws["bucket"], Key=key)
    assert obj["Body"].read() == bus_image_path.read_bytes()

    job = get_job(image_id)
    assert job["status"] == "PROCESSING"
    assert job["inputType"] == "LOCAL"

    messages = _receive_all(aws["queue_url"])
    assert {"imageId": image_id, "inputType": "LOCAL", "objectKey": key} in messages


def test_url_submission_enqueues_source_url(get_image_url, aws):
    url = "https://example.com/cat.jpg"
    resp = get_image_url.lambda_handler(_post({"type": "url", "image": url}), None)

    assert resp["statusCode"] == 200
    image_id = json.loads(resp["body"])["imageId"]
    assert get_job(image_id)["link"] == url
    assert {"imageId": image_id, "inputType": "URL", "sourceUrl": url} in _receive_all(aws["queue_url"])


@pytest.mark.parametrize("image", ["file:///etc/passwd", "ftp://example.com/a.jpg", "", None])
def test_url_submission_rejects_non_http_urls(get_image_url, image):
    resp = get_image_url.lambda_handler(_post({"type": "url", "image": image}), None)
    assert resp["statusCode"] == 400


@pytest.mark.parametrize(
    "event",
    [
        {},                                                    # no body
        {"body": "not json"},                                  # invalid JSON
        _post({"type": "video", "image": "x"}),                # unsupported type
    ],
)
def test_bad_requests_return_400(get_image_url, event):
    assert get_image_url.lambda_handler(event, None)["statusCode"] == 400


def test_base64_encoded_request_body_is_decoded(get_image_url):
    raw = json.dumps({"type": "url", "image": "https://example.com/dog.jpg"}).encode()
    event = {"body": base64.b64encode(raw).decode(), "isBase64Encoded": True}
    assert get_image_url.lambda_handler(event, None)["statusCode"] == 200


# --- check_image_status -----------------------------------------------------------

def test_status_missing_image_id_returns_400(check_image_status):
    assert check_image_status.lambda_handler({"pathParameters": None}, None)["statusCode"] == 400


def test_status_unknown_job_reports_initializing(check_image_status):
    resp = check_image_status.lambda_handler({"pathParameters": {"imageId": str(uuid.uuid4())}}, None)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"]) == {"status": "INITIALIZING"}


def test_status_returns_job_record(check_image_status, aws):
    image_id = str(uuid.uuid4())
    boto3.resource("dynamodb").Table(aws["table"]).put_item(
        Item={"imageId": image_id, "status": "COMPLETE", "message": "Found 1 object: 1 dog."}
    )
    resp = check_image_status.lambda_handler({"pathParameters": {"imageId": image_id}}, None)
    body = json.loads(resp["body"])
    assert body["status"] == "COMPLETE"
    assert body["message"] == "Found 1 object: 1 dog."

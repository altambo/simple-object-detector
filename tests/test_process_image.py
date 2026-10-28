"""Tests for the SOD_processImage container handler (backend-container/app.py)."""
import json
import os
import shutil
import socket
import uuid

import boto3
import pytest

from conftest import get_job


def _sqs_event(*bodies):
    return {"Records": [{"body": json.dumps(b)} for b in bodies]}


# --- URL safety guard -------------------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/a.jpg",
        "http://169.254.169.254/latest/meta-data/",  # instance metadata endpoint
        "http://127.0.0.1/",
        "http://localhost:8080/",
        "http://10.0.0.1/x.jpg",
        "http://192.168.1.10/x.jpg",
        "http://[::1]/",
    ],
)
def test_check_public_url_blocks_internal_and_non_http(app, url):
    with pytest.raises(ValueError):
        app.check_public_url(url)


def test_check_public_url_allows_public_host(app, monkeypatch):
    public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443))]
    monkeypatch.setattr(app.socket, "getaddrinfo", lambda *a, **k: public)
    app.check_public_url("https://images.example.com/bus.jpg")


def test_check_public_url_blocks_hostname_resolving_to_private_ip(app, monkeypatch):
    private = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 80))]
    monkeypatch.setattr(app.socket, "getaddrinfo", lambda *a, **k: private)
    with pytest.raises(ValueError):
        app.check_public_url("http://innocent-looking.example.com/x.jpg")


# --- handler -----------------------------------------------------------------------

def test_local_upload_is_processed_end_to_end(app, aws, bus_image_path):
    image_id = str(uuid.uuid4())
    key = f"data/test/{image_id}.jpg"
    boto3.client("s3").upload_file(str(bus_image_path), aws["bucket"], key)

    resp = app.lambda_handler(_sqs_event({"inputType": "LOCAL", "imageId": image_id, "objectKey": key}), None)

    assert resp["statusCode"] == 200
    job = get_job(image_id)
    assert job["status"] == "COMPLETE"
    assert "bus" in job["message"] and "person" in job["message"]
    assert image_id in job["s3OutputUrl"]
    # The annotated image was uploaded next to the input, under processed_<id>.jpg
    listed = boto3.client("s3").list_objects_v2(Bucket=aws["bucket"], Prefix="data/")
    assert any(o["Key"].endswith(f"processed_{image_id}.jpg") for o in listed["Contents"])


def test_url_input_is_downloaded_and_processed(app, bus_image_path, monkeypatch):
    image_id = str(uuid.uuid4())
    calls = []

    def fake_download(url, local_path):
        calls.append(url)
        shutil.copy(bus_image_path, local_path)

    monkeypatch.setattr(app, "download_public_image", fake_download)
    url = "https://example.com/bus.jpg"
    app.lambda_handler(_sqs_event({"inputType": "URL", "imageId": image_id, "sourceUrl": url}), None)

    assert calls == [url]
    assert get_job(image_id)["status"] == "COMPLETE"


def test_internal_url_marks_job_failed(app):
    image_id = str(uuid.uuid4())
    event = _sqs_event({"inputType": "URL", "imageId": image_id, "sourceUrl": "http://169.254.169.254/"})
    app.lambda_handler(event, None)

    job = get_job(image_id)
    assert job["status"] == "FAILED"
    assert "public address" in job["message"]


def test_non_image_file_marks_job_failed(app, aws):
    image_id = str(uuid.uuid4())
    key = f"data/test/{image_id}.jpg"
    boto3.client("s3").put_object(Bucket=aws["bucket"], Key=key, Body=b"this is not an image")

    app.lambda_handler(_sqs_event({"inputType": "LOCAL", "imageId": image_id, "objectKey": key}), None)

    job = get_job(image_id)
    assert job["status"] == "FAILED"
    assert job["message"] == "The file is not a supported image format."


def test_temp_files_are_removed_after_processing(app, aws, bus_image_path):
    image_id = str(uuid.uuid4())
    key = f"data/test/{image_id}.jpg"
    boto3.client("s3").upload_file(str(bus_image_path), aws["bucket"], key)

    app.lambda_handler(_sqs_event({"inputType": "LOCAL", "imageId": image_id, "objectKey": key}), None)

    assert not os.path.exists(f"/tmp/{image_id}.jpg")
    assert not os.path.exists(f"/tmp/processed_{image_id}.jpg")


def test_unknown_input_type_marks_job_failed(app):
    image_id = str(uuid.uuid4())
    resp = app.lambda_handler(_sqs_event({"inputType": "BOGUS", "imageId": image_id}), None)

    assert resp["statusCode"] == 200
    job = get_job(image_id)
    assert job["status"] == "FAILED"
    assert "Unsupported input type" in job["message"]


# --- batches (regression tests: one bad message must not affect the others) ------------

def _upload_bus(aws, bus_image_path, image_id):
    key = f"data/test/{image_id}.jpg"
    boto3.client("s3").upload_file(str(bus_image_path), aws["bucket"], key)
    return {"inputType": "LOCAL", "imageId": image_id, "objectKey": key}


def test_bad_message_does_not_stop_rest_of_batch(app, aws, bus_image_path):
    bad_id, good_id = str(uuid.uuid4()), str(uuid.uuid4())
    event = _sqs_event({"inputType": "BOGUS", "imageId": bad_id}, _upload_bus(aws, bus_image_path, good_id))

    app.lambda_handler(event, None)

    assert get_job(bad_id)["status"] == "FAILED"
    assert get_job(good_id)["status"] == "COMPLETE"


def test_malformed_message_does_not_change_previous_job(app, aws, bus_image_path):
    good_id = str(uuid.uuid4())
    event = _sqs_event(_upload_bus(aws, bus_image_path, good_id))
    event["Records"].append({"body": "this is not json"})

    resp = app.lambda_handler(event, None)

    assert resp["statusCode"] == 200
    assert get_job(good_id)["status"] == "COMPLETE"


def test_message_without_image_id_is_skipped(app):
    resp = app.lambda_handler(_sqs_event({"inputType": "URL", "sourceUrl": "https://example.com/a.jpg"}), None)
    assert resp["statusCode"] == 200


import logging
import os
import boto3
import json
import socket
import ipaddress
import urllib.parse
import urllib.request
import datetime

from PIL import UnidentifiedImageError

from detector import YoloOnnxDetector, load_image, annotate, summarize

# Only load a .env file if running locally (AWS sets AWS_LAMBDA_FUNCTION_NAME automatically)
if not os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    from dotenv import load_dotenv
    load_dotenv()

# Set the Logger
LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

# Fetch resource names from environment variables set in your infrastructure template
UPLOAD_BUCKET = os.environ.get('TARGET_BUCKET')

# initialize database and s3 client
S3_CLIENT = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
DYNAMODB_TABLE = dynamodb.Table( os.environ.get('DATABASE_NAME') )

# Environment variables
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "yolov8m.onnx")
MODEL = YoloOnnxDetector(MODEL_PATH, conf_threshold=0.25)

# Limits for downloading user-supplied image URLs
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 10


def check_public_url(url):
    """
    Raise ValueError unless 'url' is http(s) and resolves only to public IP addresses.
    Blocks file://, localhost, private networks and the instance metadata endpoint (SSRF).
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Only http(s) image URLs are allowed.")
    default_port = 443 if parsed.scheme == "https" else 80
    try:
        addr_info = socket.getaddrinfo(parsed.hostname, parsed.port or default_port)
    except socket.gaierror:
        raise ValueError(f"Could not resolve host {parsed.hostname}.")
    for info in addr_info:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise ValueError("Image URL must point to a public address.")


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-validate every redirect target so a public URL cannot bounce to an internal one."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


URL_OPENER = urllib.request.build_opener(SafeRedirectHandler)


def download_public_image(url, local_path):
    """Download a user-supplied image URL after SSRF and size checks."""
    check_public_url(url)
    req = urllib.request.Request(url, headers={'User-Agent': 'AWS-Lambda-YOLO'})
    with URL_OPENER.open(req, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
        data = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise ValueError(f"Image is larger than {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB.")
    with open(local_path, 'wb') as out_file:
        out_file.write(data)


def update_job_status(image_id, status, progress_message, out_url=None):
    """
    Update the status of 'image_id' in the database
    """
    if not image_id:
        LOGGER.warning(f"No image ID to update status for. Status was: {status} ({progress_message})")
        return
    # Use expression aliases (#m and #s) separated by a comma
    update_expression = "SET #m = :msg_val, #s = :status_val"
    # map your placeholder aliases to the real reserved words
    attribute_names = {
        '#m': 'message',
        '#s': 'status'
    }
    # Supply the values to inject into the expression
    attribute_values = {
        ':msg_val': progress_message,
        ':status_val': status
    }
    if out_url:
        update_expression += ", s3OutputUrl = :url"
        attribute_values[':url'] = out_url

    LOGGER.info(f"Updating DB item for image ID: {image_id}")
    LOGGER.info(f"Progress message is: {progress_message}")
    DYNAMODB_TABLE.update_item(
        Key = {'imageId': image_id},
        UpdateExpression = update_expression,
        ExpressionAttributeNames = attribute_names,
        ExpressionAttributeValues = attribute_values
    )


def lambda_handler(event, context):
    print(f'Incoming event is: {event}')
    for record in event["Records"]:
        # Reset per record so an error never reports against the previous record's job
        image_id = None
        local_input_path = ""
        local_output_file = ""
        try:
            body = json.loads(record['body'])
            image_id = body.get('imageId')
            input_type = body.get('inputType')
            if not image_id:
                raise ValueError("Message has no imageId.")

            # ---- PATHWAY 1: It is a custom SQS message from a Web URL ----
            if input_type == 'URL':
                source_url = body['sourceUrl']
                local_input_path = f"/tmp/{image_id}.jpg"
                
                LOGGER.info(f"Downloading remote image URL for ID: {image_id}")
                # Download the image from the internet (public addresses only)
                download_public_image(source_url, local_input_path)
                LOGGER.info(f"... Download completed to {local_input_path}")

            # ---- PATHWAY 2: It is an auto-triggered S3 Event from a Local File upload ----
            elif input_type == 'LOCAL':
                # extract info from body and make local download path
                object_key = body['objectKey']
                local_input_path = f"/tmp/{image_id}.jpg"
                
                LOGGER.info(f"Downloading S3 object with ID: {image_id} to {local_input_path} ...")
                S3_CLIENT.download_file(UPLOAD_BUCKET, object_key, local_input_path)
                LOGGER.info(f"... Download completed to {local_input_path}")
            
            else:
                # Skip just this record; returning here would drop the rest of the batch,
                # since SQS deletes every message once the invocation succeeds
                LOGGER.error(f"Skipping message with unsupported inputType: {input_type!r}")
                update_job_status(image_id, "FAILED", f"Unsupported input type: {input_type}")
                continue

            # 6. Process image and save output image showing detection regions
            LOGGER.info(f"Processing image")
            image = load_image(local_input_path)
            detections = MODEL.detect(image)
            summary = summarize(detections)
            LOGGER.info(summary)
            local_output_file = f"/tmp/processed_{image_id}.jpg"
            annotate(image, detections).save(local_output_file, format="JPEG", quality=95)

            # Computed per request so warm containers don't reuse a stale date
            today = datetime.date.today()
            output_key = f"data/{today:%Y}/{today:%m}/processed_{image_id}.jpg"
            
            # 6. Upload the annotated image back to S3
            LOGGER.info(f"Uploading annotated image to s3 bucket.")
            S3_CLIENT.upload_file(
                local_output_file, 
                UPLOAD_BUCKET, 
                output_key,
                ExtraArgs={'ContentType': 'image/jpeg'},
            )
            
            # 7. Generate an output url that will expire in 15 mins
            # Request a time-limited download URL from S3
            output_url = S3_CLIENT.generate_presigned_url(
                ClientMethod='get_object',
                Params={
                    'Bucket': UPLOAD_BUCKET,
                    'Key': output_key
                },
                ExpiresIn=15*60
            )
            update_job_status(image_id, "COMPLETE", summary, out_url=output_url)

        except UnidentifiedImageError:
            LOGGER.error("Downloaded file is not a readable image.")
            update_job_status(image_id, "FAILED", "The file is not a supported image format.")
        except (ValueError, FileNotFoundError) as e:
            LOGGER.error(f"Error encountered. Message is: {str(e)}")
            update_job_status(image_id, "FAILED", str(e))
        except KeyboardInterrupt:
            LOGGER.warning(f"Keyboard interrupt signal received. Exiting.")
            update_job_status(image_id, "FAILED", "Interrupted by the user with ctrl+c")
        except Exception as e:
            LOGGER.exception(f"Error in execution. Message: {str(e)}")
            update_job_status(image_id, "FAILED", str(e))
        finally:
            # Cleanup disk files to prevent storage exhaustion on warm containers
            for path in [local_input_path, local_output_file]:
                if path and os.path.exists(path):
                    os.remove(path)

    return {
        "statusCode": 200, 
        "body": json.dumps({"message": "Completed."})
    }
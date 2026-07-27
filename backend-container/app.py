
import logging
import os
import boto3
import json
import urllib
import datetime

from ultralytics import YOLO

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
MODEL = YOLO(model="yolov8m.onnx", task='detect')

# get today's date in YYYY, mm, dd
today = datetime.date.today()
yyyy = today.strftime("%Y")
mm = today.strftime("%m")
dd = today.strftime("%d")


def update_job_status(image_id, status, progress_message, out_url=None):
    """
    Update the status of 'image_id' in the database
    """
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
    global MODEL
    
    print(f'Incoming event is: {event}')
    for record in event["Records"]:
        try:
            body = json.loads(record['body'])
            local_input_path = ""
            
            # ---- PATHWAY 1: It is a custom SQS message from a Web URL ----
            if 'inputType' in body and body['inputType'] == 'URL':
                image_id = body['imageId']
                source_url = body['sourceUrl']
                local_input_path = f"/tmp/{image_id}.jpg"
                
                LOGGER.info(f"Downloading remote image URL for ID: {image_id}")
                # Download the image from the internet directly into Lambda memory
                req = urllib.request.Request(source_url, headers={'User-Agent': 'AWS-Lambda-YOLO'})
                with urllib.request.urlopen(req) as response, open(local_input_path, 'wb') as out_file:
                    out_file.write(response.read())
                LOGGER.info(f"... Download completed to {local_input_path}")

            # ---- PATHWAY 2: It is an auto-triggered S3 Event from a Local File upload ----
            elif 'inputType' in body and body['inputType'] == 'LOCAL':
                # extract info from body and make local download path
                object_key = body['objectKey']
                image_id = body['imageId']
                local_input_path = f"/tmp/{image_id}.jpg"
                
                LOGGER.info(f"Downloading S3 object with ID: {image_id} to {local_input_path} ...")
                S3_CLIENT.download_file(UPLOAD_BUCKET, object_key, local_input_path)
                LOGGER.info(f"... Download completed to {local_input_path}")
            
            else:
                return {
                    "statusCode": 400,
                    # "headers": CORS_HEADERS,
                    "body": json.dumps({"error": "Invalid engine type argument specified."})
                }

            # 6. Process image and save output image showing detection regions
            LOGGER.info(f"Processing image")
            results = MODEL(
                source = local_input_path,
                save = True,
                project="/tmp", 
                name="yolo_out", 
                exist_ok=True,
                conf=0.25
            )
            # get output path
            for r in results:
                LOGGER.info(f"Local output dir is {r.save_dir}")
                local_output_file = os.path.join(r.save_dir, f"{image_id}.jpg")

            output_key = f"data/{yyyy}/{mm}/processed_{image_id}.jpg"
            
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
            # output_url = f"https://{UPLOAD_BUCKET}/{output_key}"
            update_job_status(image_id, "COMPLETE", "Result uploaded to s3.", out_url=output_url)
            
            LOGGER.info("Clean up ...")
            # 7. Cleanup disk files to prevent storage exhaustion on warm containers
            for path in [local_input_path, local_output_file]:
                if os.path.exists(path):
                    os.remove(path)

        except (ValueError, FileNotFoundError) as e:
            LOGGER.error(f"Error encountered. Message is: {str(e)}")
            update_job_status(image_id, "FAILED", str(e))
        except KeyboardInterrupt:
            LOGGER.warning(f"Keyboard interrupt signal received. Exiting.")
            update_job_status(image_id, "FAILED", "Interrupted by the user with ctrl+c")
        except Exception as e:
            LOGGER.exception(f"Error in execution. Message: {str(e)}")
            update_job_status(image_id, "FAILED", str(e))
            

    return {
        "statusCode": 200, 
        "body": json.dumps({"message": "Completed."})
    }
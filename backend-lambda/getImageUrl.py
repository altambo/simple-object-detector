import json
import os
import uuid
import boto3
import base64
import datetime
from botocore.config import Config

# Only load a .env file if running locally (AWS sets AWS_LAMBDA_FUNCTION_NAME automatically)
if not os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    from dotenv import load_dotenv
    load_dotenv()

print("Beginning getImageURL function")
# Initialize AWS SDK clients outside the handler for container reuse
config = Config(connect_timeout=2, read_timeout=3, retries={'max_attempts': 2})
S3_CLIENT = boto3.client('s3', config=config)
SQS_CLIENT = boto3.client('sqs', config=config)
dynamodb = boto3.resource('dynamodb', config=config)

# Fetch resource names from environment variables set in your infrastructure template
UPLOAD_BUCKET = os.environ.get('UPLOAD_BUCKET')
SQS_QUEUE_URL = os.environ.get('SQS_QUEUE_URL')
DYNAMODB_TABLE = dynamodb.Table( os.environ.get('DYNAMODB_TABLE') )

# get today's date in YYYY, mm, dd
today = datetime.date.today()
yyyy = today.strftime("%Y")
mm = today.strftime("%m")
dd = today.strftime("%d")

def lambda_handler(event, context):
    """
    Upload the image (local or url) to S3 bucket and return an imageId and URL
    """
    print(f"Event is: {event}")

    try:
        # 1. Parse and extract body early
        body_str = event.get('body')
        if not body_str:
            return {'statusCode': 400, 'body': json.dumps({'error': 'Missing request body'})}
            
        if event.get('isBase64Encoded', False):
            body_str = base64.b64decode(body_str).decode('utf-8')
            
        body = json.loads(body_str)
    except (json.JSONDecodeError, ValueError, TypeError):
        return {'statusCode': 400, 'body': json.dumps({'error': 'Invalid JSON structure payload'})}

    user_city = str(body.get('userCity'))
    user_state = str(body.get('userState'))
    user_country = str(body.get('userCountry'))
    print(f"City: {user_city} | state: {user_state} | country: {user_country}")

    image_type = str(body.get('type')).lower()
    if image_type not in ('url', 'local'):
        return {'statusCode': 400, 'body': json.dumps({'error': 'Invalid image type. Must be "url" or "local"'})}

    # Generate transaction variables
    image_id = str(uuid.uuid4())
    # now = datetime.utcnow() # Fixed undefined date variables by generating from utcnow
    now = datetime.datetime.now()

    try:
        # --- PATHWAY A: Remote Image Web URL ---
        if image_type == 'url':
            object_db_link = body.get('image')
            input_type = 'URL'
            # Queue payload
            sqs_payload = {
                'imageId': image_id,
                'inputType': input_type,
                'sourceUrl': object_db_link,
            }
            return_dict = {"imageId": image_id}

        # --- PATHWAY B: Local Base64 File Data ---
        else:
            # Generate dynamic S3 folder path from the actual execution timestamp
            object_key = f"data/{yyyy}/{mm}/{image_id}.jpg"
            base64_image_data = body.get('image')
            input_type = 'LOCAL'

            # Clean and isolate base64 string
            if isinstance(base64_image_data, list):
                base64_string = base64_image_data[0] if base64_image_data else ""
            else:
                base64_string = base64_image_data or ""
                
            image_binary = base64.b64decode(base64_string)

            # Fast direct binary upload
            S3_CLIENT.put_object(
                Bucket=UPLOAD_BUCKET,
                Key=object_key,
                Body=image_binary,
                ContentType='image/jpeg'
            )
            # Queue payload
            sqs_payload = {
                'imageId': image_id,
                'inputType': input_type,
                'objectKey': object_db_link
            }
            object_db_link = object_key
            return_dict = {"imageId": image_id, "processedImage": base64_string}
        
        # === Common Downstream Operations ====
        # Write to dynamo table
        DYNAMODB_TABLE.put_item(Item={
            'imageId': image_id,
            'status': 'PROCESSING',
            'link': object_db_link,
            'inputType': input_type,
            'message': 'Initializing',
            'updateTime': now.strftime("%Y-%m-%dT%H:%M:%S"),
            'location': f"{user_city}, {user_state}, {user_country}"
        })
        # Put message in queue
        SQS_CLIENT.send_message(
            QueueUrl=SQS_QUEUE_URL,
            MessageBody=json.dumps(sqs_payload)
        )
        # return
        return {
            'statusCode': 200,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps(return_dict)
        }
        
    except Exception as e:
        # Log exact error trace to CloudWatch without breaking client security boundaries
        print(f"Error initializing processing sequence: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': 'Internal server pipeline error'})
        }


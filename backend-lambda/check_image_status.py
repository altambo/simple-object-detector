import json
import os
import boto3
from botocore.config import Config

config = Config(connect_timeout=2, read_timeout=3, retries={'max_attempts': 2})
dynamodb = boto3.resource('dynamodb', config=config)
DYNAMODB_TABLE = dynamodb.Table( os.environ.get('DATABASE_NAME') )

# print('let the games begin')
def lambda_handler(event, context):
    try:
        # print(f'event is: {event}')
        # 1. Safely extract query parameters from the API Gateway event
        # query_params = event.get('queryStringParameters') or {}
        query_params = event.get('pathParameters') or {}
        image_id = query_params.get('imageId')
        # print(f'image_id: {image_id}')
        
        # 2. Return an error if the frontend forgot to send the ID
        if not image_id:
            return {
                'statusCode': 400,
                # 'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
                'body': json.dumps({'error': 'Missing imageId parameter'})
            }
            
        # 3. Look up the specific record in DynamoDB
        response = DYNAMODB_TABLE.get_item(Key={'imageId': image_id})
        item = response.get('Item')

        if not item:
            return {
                'statusCode': 200,
                # 'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
                'body': json.dumps({'status': 'INITIALIZING'})
            }
            
        return {
            'statusCode': 200,
            # 'headers': {'Content-Type': 'application/json','Access-Control-Allow-Origin': '*'},
            'body': json.dumps(item)
        }
        
    except Exception as e:
        print(f"Error querying DynamoDB: {str(e)}")
        return {
            'statusCode': 500,
            # 'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'error': 'Failed to resolve database status'})
        }

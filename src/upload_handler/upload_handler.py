"""
Lambda function to handle document uploads using presigned S3 URLs.
Avoids sending file content through API Gateway (10MB limit, 29s timeout).
"""
import os
import json
import boto3
import logging
import uuid
import psycopg2
from datetime import datetime

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
secretsmanager = boto3.client('secretsmanager')
lambda_client = boto3.client('lambda')

# Get environment variables
DOCUMENTS_BUCKET = os.environ.get('DOCUMENTS_BUCKET')
METADATA_TABLE = os.environ.get('METADATA_TABLE')
DB_SECRET_ARN = os.environ.get('DB_SECRET_ARN')
STAGE = os.environ.get('STAGE')


def get_postgres_credentials():
    """
    Get PostgreSQL credentials from Secrets Manager.
    """
    try:
        secret_response = secretsmanager.get_secret_value(
            SecretId=DB_SECRET_ARN
        )
        secret = json.loads(secret_response['SecretString'])
        return secret
    except Exception as e:
        logger.error(f"Error getting PostgreSQL credentials: {str(e)}")
        raise e


def get_postgres_connection(credentials):
    """
    Get a connection to PostgreSQL.
    """
    conn = psycopg2.connect(
        host=credentials['host'],
        port=credentials['port'],
        user=credentials['username'],
        password=credentials['password'],
        dbname=credentials['dbname']
    )
    return conn


def get_mime_type(file_name):
    """
    Determine MIME type from file extension.

    Args:
        file_name (str): File name

    Returns:
        str: MIME type
    """
    file_extension = file_name.split('.')[-1].lower()
    mime_types = {
        'pdf':  'application/pdf',
        'txt':  'text/plain',
        'csv':  'text/csv',
        'doc':  'application/msword',
        'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'xls':  'application/vnd.ms-excel',
        'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'json': 'application/json',
        'md':   'text/markdown'
    }
    return mime_types.get(file_extension, 'application/octet-stream')


def _response(status_code, body):
    """Helper to build a consistent API Gateway proxy response."""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*'
        },
        'body': json.dumps(body)
    }


def _store_metadata(document_id, user_id, file_name, mime_type, s3_key):
    """
    Persist document metadata to PostgreSQL with DynamoDB fallback.
    """
    try:
        credentials = get_postgres_credentials()
        conn = get_postgres_connection(credentials)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO documents
                (document_id, user_id, file_name, mime_type, status, bucket, key, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            document_id,
            user_id,
            file_name,
            mime_type,
            'uploaded',
            DOCUMENTS_BUCKET,
            s3_key,
            datetime.now(),
            datetime.now()
        ))

        conn.commit()
        cursor.close()
        conn.close()
        logger.info(f"Metadata stored in PostgreSQL for document {document_id}")

    except Exception as e:
        logger.error(f"Error storing metadata in PostgreSQL: {str(e)}")
        # Fallback to DynamoDB
        metadata_table = dynamodb.Table(METADATA_TABLE)
        metadata_table.put_item(
            Item={
                'id':          f"doc#{document_id}",
                'document_id': document_id,
                'user_id':     user_id,
                'file_name':   file_name,
                'mime_type':   mime_type,
                'status':      'uploaded',
                'bucket':      DOCUMENTS_BUCKET,
                'key':         s3_key,
                'created_at':  int(datetime.now().timestamp() * 1000),
                'updated_at':  int(datetime.now().timestamp() * 1000)
            }
        )
        logger.info(f"Metadata stored in DynamoDB fallback for document {document_id}")


def handler(event, context):
    """
    Lambda function to handle document uploads.

    Supports two operations:
      - get_upload_url  : Returns a presigned S3 PUT URL. The UI uploads
                          the file directly to S3 — no file bytes pass
                          through API Gateway or Lambda.
      - confirm_upload  : Called by the UI after the direct S3 PUT succeeds.
                          Writes document metadata to PostgreSQL / DynamoDB.

    Args:
        event (dict): API Gateway event
        context (object): Lambda context

    Returns:
        dict: API Gateway proxy response
    """
    logger.info(f"Received event keys: {list(event.keys())}")

    try:
        # ── Parse body ────────────────────────────────────────────────────────
        body = {}
        if 'body' in event:
            if isinstance(event.get('body'), str) and event.get('body'):
                try:
                    body = json.loads(event['body'])
                except json.JSONDecodeError:
                    body = {}
            elif isinstance(event.get('body'), dict):
                body = event.get('body')

        # ── Health check ──────────────────────────────────────────────────────
        if event.get('action') == 'healthcheck' or body.get('action') == 'healthcheck':
            return _response(200, {
                'message': 'Upload handler is healthy',
                'stage':   STAGE
            })

        operation = body.get('operation', 'get_upload_url')

        # ── Step 1: Return a presigned S3 PUT URL ─────────────────────────────
        # The UI uses this URL to upload the file directly to S3, completely
        # bypassing API Gateway. This eliminates the 29 s timeout and 10 MB
        # payload limit that caused the 504 errors.
        if operation == 'get_upload_url':
            file_name = body.get('file_name', '')
            mime_type = body.get('mime_type', None) or get_mime_type(file_name)
            user_id   = body.get('user_id', 'system')

            if not file_name:
                return _response(400, {'message': 'file_name is required'})

            document_id = str(uuid.uuid4())
            s3_key      = f"uploads/{user_id}/{document_id}/{file_name}"

            # Presigned PUT URL valid for 10 minutes
            presigned_url = s3_client.generate_presigned_url(
                'put_object',
                Params={
                    'Bucket':      DOCUMENTS_BUCKET,
                    'Key':         s3_key,
                    'ContentType': mime_type,
                },
                ExpiresIn=600
            )

            logger.info(f"Generated presigned URL for {file_name} (document_id={document_id})")

            return _response(200, {
                'upload_url':  presigned_url,
                'document_id': document_id,
                's3_key':      s3_key,
                'file_name':   file_name,
            })

        # ── Step 2: Confirm upload and persist metadata ───────────────────────
        # Called by the UI after the direct S3 PUT completes successfully.
        elif operation == 'confirm_upload':
            document_id = body.get('document_id', '')
            file_name   = body.get('file_name', '')
            mime_type   = body.get('mime_type', None) or get_mime_type(file_name)
            user_id     = body.get('user_id', 'system')
            s3_key      = body.get('s3_key', '')

            if not document_id or not s3_key:
                return _response(400, {'message': 'document_id and s3_key are required'})

            _store_metadata(document_id, user_id, file_name, mime_type, s3_key)

            logger.info(f"Upload confirmed for document {document_id}")

            return _response(200, {
                'message':     'File uploaded successfully',
                'document_id': document_id,
                'file_name':   file_name,
            })

        else:
            return _response(400, {'message': f"Unknown operation: '{operation}'. Use 'get_upload_url' or 'confirm_upload'."})

    except Exception as e:
        logger.error(f"Unhandled error in upload handler: {str(e)}")
        return _response(500, {'message': f"Error uploading file: {str(e)}"})
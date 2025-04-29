import os
import json
import boto3
import logging
import uuid
import time
from datetime import datetime, timedelta
import hashlib

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
sns_client = boto3.client('sns')

# Environment variables
S3_BUCKET = os.environ['S3_BUCKET']
DYNAMODB_TABLE = os.environ['DYNAMODB_TABLE']
SNS_TOPIC_ARN = os.environ['SNS_TOPIC_ARN']

def lambda_handler(event, context):
    """
    Aggregates API endpoint results from all repositories and saves to DynamoDB.
    
    Args:
        event: Lambda event data containing job information
        context: Lambda context
    
    Returns:
        Dictionary containing summary of the aggregation process
    """
    # Extract job ID from the event
    job_id = event['jobId']
    logger.info(f"Aggregating results for job: {job_id}")
    
    # List all result files from S3
    result_files = list_result_files(job_id)
    logger.info(f"Found {len(result_files)} result files to process")
    
    # Process result files and extract endpoints
    all_endpoints = []
    for result_key in result_files:
        try:
            endpoints = process_result_file(result_key)
            all_endpoints.extend(endpoints)
        except Exception as e:
            logger.error(f"Error processing result file {result_key}: {str(e)}")
    
    # Deduplicate endpoints
    unique_endpoints = deduplicate_endpoints(all_endpoints)
    logger.info(f"Extracted {len(unique_endpoints)} unique endpoints")
    
    # Store endpoints in DynamoDB
    store_endpoints_in_dynamodb(unique_endpoints)
    
    # Generate and save report
    report = generate_report(unique_endpoints, job_id)
    report_key = save_report(report, job_id)
    
    # Send SNS notification
    send_notification(report, report_key)
    
    return {
        'jobId': job_id,
        'totalEndpoints': len(unique_endpoints),
        'reportKey': report_key
    }

def list_result_files(job_id):
    """
    Lists all result files in S3 for a given job.
    
    Args:
        job_id: Job ID
    
    Returns:
        List of S3 keys for result files
    """
    result_prefix = f"results/{job_id}/"
    
    # List all objects with the prefix
    result_files = []
    paginator = s3_client.get_paginator('list_objects_v2')
    
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=result_prefix):
        if 'Contents' in page:
            for obj in page['Contents']:
                result_files.append(obj['Key'])
    
    return result_files

def process_result_file(result_key):
    """
    Processes a result file and extracts endpoints.
    
    Args:
        result_key: S3 key for the result file
    
    Returns:
        List of endpoint dictionaries
    """
    # Download and parse the result file
    response = s3_client.get_object(Bucket=S3_BUCKET, Key=result_key)
    result_data = json.loads(response['Body'].read().decode('utf-8'))
    
    # Extract endpoints
    endpoints = result_data.get('endpoints', [])
    return endpoints

def deduplicate_endpoints(endpoints):
    """
    Deduplicates endpoints based on method and path.
    
    Args:
        endpoints: List of endpoint dictionaries
    
    Returns:
        List of unique endpoint dictionaries
    """
    unique_endpoints = {}
    
    for endpoint in endpoints:
        # Create a unique key for each endpoint
        method = endpoint.get('method', '')
        path = endpoint.get('path', '')
        
        if method and path:
            key = f"{method}:{path}"
            
            if key not in unique_endpoints:
                unique_endpoints[key] = endpoint
            else:
                # If duplicate, merge source information
                existing = unique_endpoints[key]
                if endpoint.get('repo_name') != existing.get('repo_name') or endpoint.get('file_path') != existing.get('file_path'):
                    # Add alternative source if different
                    if 'alternative_sources' not in existing:
                        existing['alternative_sources'] = []
                    
                    alternative = {
                        'repo_name': endpoint.get('repo_name'),
                        'file_path': endpoint.get('file_path')
                    }
                    
                    if alternative not in existing['alternative_sources']:
                        existing['alternative_sources'].append(alternative)
    
    return list(unique_endpoints.values())

def store_endpoints_in_dynamodb(endpoints):
    """
    Stores endpoints in DynamoDB table.
    
    Args:
        endpoints: List of endpoint dictionaries
    """
    table = dynamodb.Table(DYNAMODB_TABLE)
    current_time = int(time.time())
    expiry_time = current_time + (365 * 24 * 60 * 60)  # 1 year TTL
    
    # Process endpoints in batches (to respect DynamoDB limits)
    batch_size = 25
    for i in range(0, len(endpoints), batch_size):
        batch = endpoints[i:i+batch_size]
        
        with table.batch_writer() as batch_writer:
            for endpoint in batch:
                # Create a unique ID for the endpoint
                method = endpoint.get('method', '')
                path = endpoint.get('path', '')
                repo_name = endpoint.get('repo_name', '')
                
                if method and path and repo_name:
                    # Generate a stable hash for the endpoint
                    endpoint_hash = hashlib.md5(f"{method}:{path}".encode()).hexdigest()
                    
                    # Prepare the item
                    item = {
                        'repo_path': repo_name,
                        'endpoint_id': endpoint_hash,
                        'method': method,
                        'path': path,
                        'file_path': endpoint.get('file_path', ''),
                        'last_seen': current_time,
                        'ttl': expiry_time
                    }
                    
                    # Add alternative sources if available
                    if 'alternative_sources' in endpoint:
                        item['alternative_sources'] = endpoint['alternative_sources']
                    
                    # Put the item
                    batch_writer.put_item(Item=item)

def generate_report(endpoints, job_id):
    """
    Generates a report from the extracted endpoints.
    
    Args:
        endpoints: List of endpoint dictionaries
        job_id: Job ID
    
    Returns:
        Report dictionary
    """
    # Organize endpoints by repository
    repo_endpoints = {}
    
    for endpoint in endpoints:
        repo_
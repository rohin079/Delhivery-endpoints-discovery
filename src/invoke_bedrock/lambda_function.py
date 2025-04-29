import os
import json
import boto3
import logging
import uuid
import re

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
s3_client = boto3.client('s3')
bedrock_runtime = boto3.client('bedrock-runtime')

# Environment variables
S3_BUCKET = os.environ['S3_BUCKET']
MODEL_ID = os.environ['MODEL_ID']

def lambda_handler(event, context):
    """
    Invokes Bedrock LLM to extract API endpoints from code chunks.
    
    Args:
        event: Lambda event data containing chunk information
        context: Lambda context
    
    Returns:
        Dictionary containing extracted endpoints
    """
    # Extract data from event
    chunk = event['chunk']
    repository = event['repository']
    job_id = event['jobId']
    
    repo_name = repository['name']
    file_path = chunk['file_path']
    language = chunk['language']
    content = chunk['content']
    chunk_id = chunk['chunk_id']
    is_partial = chunk.get('is_partial', False)
    
    logger.info(f"Processing chunk {chunk_id} from {repo_name}/{file_path}")
    
    # Skip empty or very small chunks
    if not content or len(content) < 50:
        logger.info(f"Skipping chunk {chunk_id} - content too small")
        return {
            'repo_name': repo_name,
            'file_path': file_path,
            'chunk_id': chunk_id,
            'endpoints': []
        }
    
    # Craft prompt for LLM
    prompt = create_endpoint_extraction_prompt(content, language, file_path, is_partial)
    
    try:
        # Invoke Bedrock model
        response = invoke_llm(prompt)
        
        # Extract endpoints from LLM response
        endpoints = parse_endpoints_from_response(response, repo_name, file_path)
        
        logger.info(f"Extracted {len(endpoints)} endpoints from {repo_name}/{file_path}")
        
        # Save results to S3
        result = {
            'repo_name': repo_name,
            'file_path': file_path,
            'chunk_id': chunk_id,
            'endpoints': endpoints
        }
        
        result_key = f"results/{job_id}/{repo_name}/{uuid.uuid4()}.json"
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=result_key,
            Body=json.dumps(result),
            ContentType='application/json'
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Error processing chunk {chunk_id} from {repo_name}/{file_path}: {str(e)}")
        return {
            'repo_name': repo_name,
            'file_path': file_path,
            'chunk_id': chunk_id,
            'error': str(e),
            'endpoints': []
        }

def create_endpoint_extraction_prompt(content, language, file_path, is_partial):
    """
    Creates a prompt for the LLM to extract API endpoints.
    
    Args:
        content: Code content to analyze
        language: Programming language of the code
        file_path: Path of the file
        is_partial: Whether this is a partial chunk
    
    Returns:
        Formatted prompt string
    """
    prompt = f"""
You are an expert API endpoint analyzer. Your task is to extract ALL HTTP API endpoints (methods and paths) from the provided code.

File: {file_path}
Language: {language}
{'This is a partial chunk of a larger file.' if is_partial else 'This is a complete file or section.'}

CODE:
```
{content}
```

Please extract ALL API endpoints with their HTTP methods and paths. For each endpoint found, provide:
1. The HTTP method (GET, POST, PUT, DELETE, PATCH, etc.)
2. The path (e.g., "/api/users", "/v1/products/{id}")

Only include actual endpoints, not helper functions or middleware. If endpoints include variables, represent them as path parameters in curly braces like "/users/{id}" or "/products/{productId}".

Format your response as a JSON array with "method" and "path" properties for each endpoint. Example:
[
  {{"method": "GET", "path": "/api/users"}},
  {{"method": "POST", "path": "/api/users"}},
  {{"method": "GET", "path": "/api/users/{id}"}}
]

If you don't find any endpoints, return an empty array: []
"""
    return prompt

def invoke_llm(prompt):
    """
    Invokes the Bedrock LLM with the given prompt.
    
    Args:
        prompt: Prompt string
    
    Returns:
        LLM response text
    """
    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1500,
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ]
    }
    
    response = bedrock_runtime.invoke_model(
        modelId=MODEL_ID,
        body=json.dumps(request_body)
    )
    
    response_body = json.loads(response["body"].read().decode())
    return response_body["content"][0]["text"]

def parse_endpoints_from_response(response_text, repo_name, file_path):
    """
    Parses endpoints from the LLM response.
    
    Args:
        response_text: LLM response text
        repo_name: Repository name
        file_path: File path
    
    Returns:
        List of endpoint dictionaries
    """
    # Try to extract JSON from the response
    json_match = re.search(r'\[\s*\{.*\}\s*\]', response_text, re.DOTALL)
    
    endpoints = []
    
    if json_match:
        try:
            extracted_json = json_match.group(0)
            endpoints_data = json.loads(extracted_json)
            
            # Process and clean endpoints
            for endpoint in endpoints_data:
                if isinstance(endpoint, dict) and 'method' in endpoint and 'path' in endpoint:
                    # Normalize method to uppercase
                    method = endpoint['method'].upper()
                    
                    # Clean and normalize path
                    path = endpoint['path']
                    if path and isinstance(path, str):
                        # Remove duplicate slashes and ensure leading slash
                        path = re.sub(r'/+', '/', path)
                        if not path.startswith('/'):
                            path = '/' + path
                        
                        # Add endpoint with source information
                        endpoints.append({
                            'method': method,
                            'path': path,
                            'repo_name': repo_name,
                            'file_path': file_path
                        })
        except Exception as e:
            logger.error(f"Error parsing JSON from LLM response: {str(e)}")
            # Try regex-based extraction as fallback
            endpoints = regex_extract_endpoints(response_text, repo_name, file_path)
    else:
        # Try regex-based extraction as fallback
        endpoints = regex_extract_endpoints(response_text, repo_name, file_path)
    
    return endpoints

def regex_extract_endpoints(text, repo_name, file_path):
    """
    Fallback method to extract endpoints using regex patterns.
    
    Args:
        text: Text to parse
        repo_name: Repository name
        file_path: File path
    
    Returns:
        List of endpoint dictionaries
    """
    endpoints = []
    
    # Pattern to match "METHOD" and "/path" in various formats
    patterns = [
        r'(?:"|\')?method(?:"|\')?:\s*(?:"|\')?([A-Z]+)(?:"|\')?.*?(?:"|\')?path(?:"|\')?:\s*(?:"|\')?([^"\'}\s]+)',
        r'([A-Z]+)\s+(?:"|\')([/][^"\'}\s]+)',
        r'method:\s*([A-Z]+).*?path:\s*([/][^,}\s]+)'
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            if len(match) == 2:
                method, path = match
                
                # Normalize path
                if path and isinstance(path, str):
                    path = re.sub(r'/+', '/', path)
                    if not path.startswith('/'):
                        path = '/' + path
                
                endpoints.append({
                    'method': method,
                    'path': path,
                    'repo_name': repo_name,
                    'file_path': file_path
                })
    
    return endpoints
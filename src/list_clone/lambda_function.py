import os
import json
import boto3
import uuid
import subprocess
import tempfile
import shutil
import base64
import logging
from urllib.request import Request, urlopen

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
s3_client = boto3.client('s3')
secretsmanager_client = boto3.client('secretsmanager')

# Environment variables
S3_BUCKET = os.environ['S3_BUCKET']
GITHUB_PAT_SECRET_ID = os.environ['GITHUB_PAT_SECRET_ID']
GITHUB_ORG = os.environ['GITHUB_ORG']
GITHUB_API_URL = f"https://api.github.com/orgs/{GITHUB_ORG}/repos"

def lambda_handler(event, context):
    """
    Lists all repositories from the GitHub organization and performs shallow clones.
    
    Args:
        event: Lambda event data
        context: Lambda context
    
    Returns:
        Dictionary containing job ID and list of repositories
    """
    # Generate a unique job ID for this run
    job_id = str(uuid.uuid4())
    logger.info(f"Starting API endpoint discovery job: {job_id}")
    
    # Get GitHub PAT from Secrets Manager
    github_pat = get_github_pat()
    
    # List all repositories in the GitHub organization
    repos = list_repositories(github_pat)
    logger.info(f"Found {len(repos)} repositories in organization {GITHUB_ORG}")
    
    # Create a temporary directory for cloning
    with tempfile.TemporaryDirectory() as temp_dir:
        repositories = []
        
        # Process each repository
        for repo in repos:
            repo_name = repo['name']
            repo_clone_url = repo['clone_url']
            repo_default_branch = repo.get('default_branch', 'main')
            
            logger.info(f"Processing repository: {repo_name}")
            
            # Clone repository with authentication and only default branch
            repo_dir = os.path.join(temp_dir, repo_name)
            auth_url = repo_clone_url.replace("https://", f"https://x-access-token:{github_pat}@")
            
            try:
                # Perform shallow clone (depth=1) to minimize data transfer
                subprocess.run(
                    ["git", "clone", "--depth", "1", "--single-branch", "--branch", repo_default_branch, auth_url, repo_dir],
                    check=True,
                    capture_output=True
                )
                
                # Create a zip archive of the repository
                repo_zip_path = os.path.join(temp_dir, f"{repo_name}.zip")
                shutil.make_archive(os.path.join(temp_dir, repo_name), 'zip', repo_dir)
                
                # Upload repository zip to S3
                s3_key = f"repos/{job_id}/{repo_name}.zip"
                s3_client.upload_file(repo_zip_path, S3_BUCKET, s3_key)
                
                # Add repository information to the list
                repositories.append({
                    'name': repo_name,
                    's3_key': s3_key,
                    'default_branch': repo_default_branch
                })
                
            except Exception as e:
                logger.error(f"Error processing repository {repo_name}: {str(e)}")
                continue
    
    # Return job ID and repositories
    return {
        'jobId': job_id,
        'repositories': repositories
    }

def get_github_pat():
    """
    Retrieves GitHub Personal Access Token from Secrets Manager.
    
    Returns:
        String containing the GitHub PAT
    """
    try:
        response = secretsmanager_client.get_secret_value(SecretId=GITHUB_PAT_SECRET_ID)
        secret_data = json.loads(response['SecretString'])
        return secret_data['github_pat']
    except Exception as e:
        logger.error(f"Error retrieving GitHub PAT: {str(e)}")
        raise

def list_repositories(github_pat):
    """
    Lists all repositories in the GitHub organization using the GitHub API.
    
    Args:
        github_pat: GitHub Personal Access Token
    
    Returns:
        List of repository information dictionaries
    """
    repos = []
    page = 1
    
    while True:
        # Set up API request with pagination
        headers = {
            'Authorization': f'token {github_pat}',
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'API-Endpoint-Discovery-Bot'
        }
        
        url = f"{GITHUB_API_URL}?per_page=100&page={page}"
        req = Request(url, headers=headers)
        
        try:
            with urlopen(req) as response:
                # Parse repositories from response
                current_repos = json.loads(response.read().decode('utf-8'))
                
                # If no more repositories, break the loop
                if not current_repos:
                    break
                
                repos.extend(current_repos)
                page += 1
                
        except Exception as e:
            logger.error(f"Error fetching repositories (page {page}): {str(e)}")
            break
    
    return repos
import os
import json
import boto3
import zipfile
import tempfile
import re
import logging
from pathlib import Path

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
s3_client = boto3.client('s3')

# Environment variables
S3_BUCKET = os.environ['S3_BUCKET']

# Patterns to identify API route files by framework/language
FILE_PATTERNS = {
    # Express.js (Node.js)
    'node_express': [
        r'routes/.*\.js$',
        r'controllers/.*\.js$',
        r'api/.*\.js$',
        r'.*router\.js$',
        r'.*routes\.js$',
        r'.*controller\.js$'
    ],
    # FastAPI/Flask (Python)
    'python_web': [
        r'routes/.*\.py$',
        r'views/.*\.py$',
        r'api/.*\.py$',
        r'controllers/.*\.py$',
        r'.*router\.py$',
        r'.*routes\.py$',
        r'.*views\.py$',
        r'.*app\.py$'
    ],
    # Spring Boot (Java)
    'java_spring': [
        r'.*Controller\.java$',
        r'.*Resource\.java$',
        r'.*Endpoint\.java$'
    ],
    # Go
    'golang': [
        r'handlers/.*\.go$',
        r'routes/.*\.go$',
        r'api/.*\.go$',
        r'.*router\.go$',
        r'.*handler\.go$'
    ],
    # TypeScript
    'typescript': [
        r'routes/.*\.ts$',
        r'controllers/.*\.ts$',
        r'api/.*\.ts$',
        r'.*router\.ts$',
        r'.*routes\.ts$',
        r'.*controller\.ts$'
    ]
}

# Function signature patterns by language to focus on relevant parts only
FUNCTION_SIGNATURES = {
    # Express.js route handlers (Node.js)
    'js': [
        r'(router|app)\.(get|post|put|delete|patch|head|options)\([^)]*\)',
        r'(router|app)\.route\([^)]*\)\.(get|post|put|delete|patch|head|options)',
        r'express\.Router\(\)'
    ],
    # Python route decorators
    'py': [
        r'@(app|router|blueprint)\.(route|get|post|put|delete|patch|head|options)',
        r'@api_view\(\[.*\]\)',
        r'class\s+\w+\(.*View\):',
        r'def\s+\w+\(.*request'
    ],
    # Java annotations
    'java': [
        r'@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping)',
        r'@Path\([^)]*\)'
    ],
    # Go handler functions
    'go': [
        r'func\s+\w+\((\s*\w+\s+[*]?gin\.Context|\s*\w+\s+[*]?http\.ResponseWriter,\s*\w+\s+[*]?http\.Request|\s*\w+\s+[*]?echo\.Context)',
        r'\.(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\('
    ],
    # TypeScript route handlers
    'ts': [
        r'(router|app)\.(get|post|put|delete|patch|head|options)\([^)]*\)',
        r'@(Get|Post|Put|Delete|Patch|Head|Options)\([^)]*\)',
        r'@ApiOperation\([^)]*\)'
    ]
}

# Maximum size for each chunk to stay within LLM context window
MAX_CHUNK_SIZE = 4000  # Characters per chunk

def lambda_handler(event, context):
    """
    Discovers API endpoint files in a repository and chunks them for LLM processing.
    
    Args:
        event: Lambda event data containing repository information
        context: Lambda context
    
    Returns:
        Dictionary containing repository and chunk information
    """
    # Extract repository information from event
    repository = event['repository']
    job_id = event['jobId']
    
    repo_name = repository['name']
    repo_s3_key = repository['s3_key']
    
    logger.info(f"Processing repository: {repo_name}")
    
    # Create temporary directory for extraction
    with tempfile.TemporaryDirectory() as temp_dir:
        # Download repository zip from S3
        zip_path = os.path.join(temp_dir, f"{repo_name}.zip")
        s3_client.download_file(S3_BUCKET, repo_s3_key, zip_path)
        
        # Extract repository
        extract_dir = os.path.join(temp_dir, repo_name)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
        
        # Discover API endpoint files
        api_files = discover_api_files(extract_dir)
        logger.info(f"Found {len(api_files)} API endpoint files in {repo_name}")
        
        # Process and chunk API files
        chunks = []
        
        for file_path in api_files:
            try:
                file_chunks = process_file(file_path, repo_name)
                chunks.extend(file_chunks)
            except Exception as e:
                logger.error(f"Error processing file {file_path}: {str(e)}")
                continue
        
        logger.info(f"Created {len(chunks)} chunks from repository {repo_name}")
        
        # Store chunks in S3
        for i, chunk in enumerate(chunks):
            chunk_id = f"{repo_name}_{i}"
            chunk_key = f"chunks/{job_id}/{chunk_id}.json"
            
            s3_client.put_object(
                Bucket=S3_BUCKET,
                Key=chunk_key,
                Body=json.dumps(chunk),
                ContentType='application/json'
            )
            
            # Update chunk with S3 location
            chunk['s3_key'] = chunk_key
        
        return {
            'repository': repository,
            'chunks': chunks
        }

def discover_api_files(repo_dir):
    """
    Discovers API endpoint files in the repository using regex patterns.
    
    Args:
        repo_dir: Path to the repository directory
    
    Returns:
        List of file paths containing potential API endpoints
    """
    api_files = []
    
    # Walk through repository directory
    for root, _, files in os.walk(repo_dir):
        for file in files:
            file_path = os.path.join(root, file)
            relative_path = os.path.relpath(file_path, repo_dir)
            
            # Skip hidden directories and files
            if any(part.startswith('.') for part in Path(relative_path).parts):
                continue
            
            # Skip node_modules, vendor directories, etc.
            if any(part in ['node_modules', 'vendor', 'dist', 'build', 'target', 'venv'] 
                  for part in Path(relative_path).parts):
                continue
            
            # Check if file matches any of the patterns
            for framework, patterns in FILE_PATTERNS.items():
                if any(re.search(pattern, relative_path, re.IGNORECASE) for pattern in patterns):
                    api_files.append((file_path, relative_path))
                    break
    
    return api_files

def process_file(file_info, repo_name):
    """
    Processes an API file and chunks it for LLM analysis.
    
    Args:
        file_info: Tuple containing (absolute_path, relative_path)
        repo_name: Name of the repository
    
    Returns:
        List of chunks for the file
    """
    abs_path, rel_path = file_info
    
    # Determine file extension
    _, ext = os.path.splitext(rel_path)
    ext = ext[1:].lower()  # Remove the dot and lowercase
    
    # Map extension to language
    lang_map = {
        'js': 'js',
        'ts': 'ts',
        'py': 'py',
        'java': 'java',
        'go': 'go'
    }
    
    lang = lang_map.get(ext, 'unknown')
    
    # Read file content
    with open(abs_path, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()
    
    # Create chunks based on language-specific patterns
    chunks = []
    
    if lang in FUNCTION_SIGNATURES:
        # Extract relevant sections using function signatures
        sections = extract_api_sections(content, lang)
        
        if not sections:
            # If no specific sections found, use the whole file as one section
            sections = [content]
    else:
        # For unknown languages, use the whole file
        sections = [content]
    
    # Create chunks from sections
    for i, section in enumerate(sections):
        # Further chunk if section is too large
        if len(section) > MAX_CHUNK_SIZE:
            sub_chunks = chunk_by_size(section, MAX_CHUNK_SIZE)
            
            for j, sub_chunk in enumerate(sub_chunks):
                chunks.append({
                    'repo_name': repo_name,
                    'file_path': rel_path,
                    'language': lang,
                    'chunk_id': f"{i}_{j}",
                    'content': sub_chunk,
                    'is_partial': True,
                    'total_chunks': len(sub_chunks)
                })
        else:
            chunks.append({
                'repo_name': repo_name,
                'file_path': rel_path,
                'language': lang,
                'chunk_id': str(i),
                'content': section,
                'is_partial': False,
                'total_chunks': 1
            })
    
    return chunks

def extract_api_sections(content, lang):
    """
    Extracts relevant API sections from file content based on language.
    
    Args:
        content: File content as string
        lang: Language identifier
    
    Returns:
        List of relevant sections
    """
    sections = []
    
    # Get patterns for the language
    patterns = FUNCTION_SIGNATURES.get(lang, [])
    
    # For each pattern
    for pattern in patterns:
        # Find all occurrences and their surrounding context
        for match in re.finditer(pattern, content):
            start = match.start()
            
            # Find beginning of the function/section (go back up to 500 chars)
            context_start = max(0, start - 500)
            context_section = content[context_start:start]
            
            # Try to find a function declaration or class before this point
            lines = context_section.split('\n')
            for i in range(len(lines) - 1, -1, -1):
                if re.search(r'(function|class|def|func)\s+\w+', lines[i]):
                    context_start = context_start + context_section.find(lines[i])
                    break
            
            # Find end of the function/section
            # First go forward to end of line
            end = content.find('\n', match.end())
            if end == -1:
                end = len(content)
            
            # Then extract the next block (up to 2000 chars after the match)
            block_end = min(len(content), end + 2000)
            block = content[end:block_end]
            
            # Try to find the end of the function/method
            brace_stack = []
            in_string = False
            string_char = None
            
            # Determine if we need to look for closing braces based on language
            if lang in ['js', 'ts', 'java', 'go']:
                # Count braces from the matching point
                for i, char in enumerate(content[match.start():block_end]):
                    if char in ['"', "'"]:
                        if not in_string:
                            in_string = True
                            string_char = char
                        elif char == string_char:
                            in_string = False
                    
                    if not in_string:
                        if char == '{':
                            brace_stack.append('{')
                        elif char == '}':
                            if brace_stack:
                                brace_stack.pop()
                            if not brace_stack and i > match.end() - match.start():
                                end = match.start() + i + 1
                                break
            
            # For Python, look for consistent indentation
            elif lang == 'py':
                lines = block.split('\n')
                if len(lines) > 1:
                    # Get the indentation of the first non-empty line
                    for i, line in enumerate(lines):
                        if line.strip():
                            first_line_indent = len(line) - len(line.lstrip())
                            break
                    
                    # Find when indentation returns to the same level or less
                    for i, line in enumerate(lines[1:], 1):
                        if line.strip() and len(line) - len(line.lstrip()) <= first_line_indent:
                            end = end + block[:block.find('\n', block.find('\n') + 1)].find('\n')
                            break
            
            # Extract the section
            section = content[context_start:end].strip()
            if section:
                sections.append(section)
    
    return sections

def chunk_by_size(text, max_size):
    """
    Chunks text by size while trying to preserve code structure.
    
    Args:
        text: Text to chunk
        max_size: Maximum chunk size
    
    Returns:
        List of text chunks
    """
    if len(text) <= max_size:
        return [text]
    
    chunks = []
    lines = text.split('\n')
    current_chunk = []
    current_size = 0
    
    for line in lines:
        line_size = len(line) + 1  # +1 for newline
        
        if current_size + line_size > max_size and current_chunk:
            # Complete current chunk
            chunks.append('\n'.join(current_chunk))
            current_chunk = []
            current_size = 0
        
        # Add line to current chunk
        current_chunk.append(line)
        current_size += line_size
    
    # Add the last chunk if not empty
    if current_chunk:
        chunks.append('\n'.join(current_chunk))
    
    return chunks
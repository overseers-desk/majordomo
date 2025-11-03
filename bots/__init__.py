"""
Common bot utilities and loader.

Provides unified logging, bot loading abstraction (works for both modules and packages),
and common helper functions for all bots.
"""

import logging
import os
import sys
import importlib

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.errors import HttpError

# Scopes needed for Chat API
SCOPES = [
    'https://www.googleapis.com/auth/chat.spaces',
    'https://www.googleapis.com/auth/chat.messages',
    'https://www.googleapis.com/auth/chat.messages.readonly',
]


def setup_logging():
    """
    Configure unified logging for all bots and dispatcher.
    
    Logging location priority:
    1. If LOG_DIR environment variable is set, use that directory
    2. Otherwise, use ../logs/google-chatbot.log (parent of project root)
    
    Logs are written to ../logs/google-chatbot.log with timestamp, level, and message.
    """
    log_dir = None
    log_file = None
    
    # Check for environment variable first
    if os.environ.get('LOG_DIR'):
        log_dir = os.environ.get('LOG_DIR')
        if os.path.isdir(log_dir):
            log_file = os.path.join(log_dir, 'google-chatbot.log')
    
    # If no env var, use ../logs/google-chatbot.log (parent of project root)
    if not log_file:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        parent_logs = os.path.join(os.path.dirname(project_root), 'logs')
        log_file = os.path.join(parent_logs, 'google-chatbot.log')
        
        # Ensure logs directory exists
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
    
    # Configure logging with source identifier
    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format='%(asctime)s - [%(name)s] - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )


def load_bot(bot_name):
    """
    Load a bot module or package dynamically.
    
    Works for both module format (bots/tachy.py) and package format (bots/raven/__init__.py).
    The bot must export a `process_event(event_data)` function.
    
    Args:
        bot_name (str): Name of the bot (e.g., 'tachy', 'raven')
    
    Returns:
        module: The loaded bot module/package
    
    Raises:
        ImportError: If the bot cannot be imported
        AttributeError: If the bot doesn't have process_event function
    """
    try:
        bot_module = importlib.import_module(f'bots.{bot_name}')
        
        # Verify it has the required interface
        if not hasattr(bot_module, 'process_event'):
            raise AttributeError(
                f"Bot '{bot_name}' must export a 'process_event' function"
            )
        
        return bot_module
    except ImportError as e:
        raise ImportError(
            f"Could not load bot '{bot_name}'. "
            f"Ensure bots/{bot_name}.py or bots/{bot_name}/__init__.py exists. "
            f"Error: {e}"
        )


def _get_bot_credentials_paths(bot_name):
    """
    Get credential file paths for a specific bot.
    
    Bots MUST use credentials from their bot-specific directory: config/bots/{bot_name}/
    
    Args:
        bot_name (str): Name of the bot (e.g., 'tachy', 'raven')
    
    Returns:
        tuple: (token_file, credentials_file) paths
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    # Bot-specific credential location (required)
    bot_token = os.path.join(project_root, 'config', 'bots', bot_name, 'token.json')
    bot_secret = os.path.join(project_root, 'config', 'bots', bot_name, 'client_secret.json')
    
    return bot_token, bot_secret


def get_bot_credentials(bot_name) -> Credentials:
    """
    Fetch or refresh Google API credentials for a specific bot.
    
    Bots MUST use credentials from config/bots/{bot_name}/ directory.
    No fallback to top-level config - bot credentials must be in bot directory.
    
    Args:
        bot_name (str): Name of the bot (e.g., 'tachy', 'raven')
    
    Returns:
        Credentials: Google API credentials object
    """
    token_file, credentials_file = _get_bot_credentials_paths(bot_name)
    
    # Verify credentials file exists
    if not os.path.exists(credentials_file):
        raise FileNotFoundError(
            f"Bot credentials not found for '{bot_name}'. "
            f"Expected: {credentials_file}\n"
            f"Please copy client_secret.json to config/bots/{bot_name}/"
        )
    
    creds = None
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
            creds = flow.run_local_server(port=7276, access_type="offline", prompt='consent')
        with open(token_file, 'w') as token:
            token.write(creds.to_json())

    return creds


def send_response_async(space_name, thread_name, response_text, bot_name='tachy'):
    """
    Send a response message to Google Chat asynchronously.
    
    Uses the Chat API with OAuth credentials from bot-specific directory.
    
    Args:
        space_name (str): The space name (e.g., spaces/ABC123)
        thread_name (str): The thread name (optional)
        response_text (str): The text to send as response
        bot_name (str): Name of the bot (default: 'tachy')
    """
    if not space_name:
        logging.error("No space name provided, cannot send response")
        return
    
    try:
        # Get bot-specific credentials and build service
        creds = get_bot_credentials(bot_name)
        service = build('chat', 'v1', credentials=creds)
        
        # Prepare the response message
        response_body = {
            'text': response_text
        }
        
        # If there's a thread, reply in that thread
        if thread_name:
            response_body['thread'] = {'name': thread_name}
        
        # Send the message
        result = service.spaces().messages().create(
            parent=space_name,
            body=response_body
        ).execute()
        
        logging.info(f"Response sent successfully to {space_name}: {response_text}")
        
    except HttpError as e:
        logging.error(f"HTTP error sending response: {e}")
        raise
    except Exception as e:
        logging.error(f"Unexpected error sending response: {e}")
        raise


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

from google_chat_reporter import get_credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


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
    
    # Configure logging
    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
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


def send_response_async(space_name, thread_name, response_text):
    """
    Send a response message to Google Chat asynchronously.
    
    Uses the Chat API with OAuth credentials to post a response
    in the same thread as the original message.
    
    Args:
        space_name (str): The space name (e.g., spaces/ABC123)
        thread_name (str): The thread name (optional)
        response_text (str): The text to send as response
    """
    if not space_name:
        logging.error("No space name provided, cannot send response")
        return
    
    try:
        # Get credentials and build service
        creds = get_credentials()
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


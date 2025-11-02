#!/usr/bin/env python3
"""
Google Chat App Handler

This module processes incoming events from Google Chat and responds to messages.
It logs key information and sends "Hey, hello" responses to all messages.
"""

import logging
import json
import os
import sys
from datetime import datetime

# Add parent directory to path to import google_chat_reporter
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google_chat_reporter import get_credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


def setup_logging():
    """
    Configure logging for the Chat App handler.
    
    Logs are written to logs/chatbot.log with timestamp, level, and message.
    Uses Python's standard logging format for consistency.
    """
    # Get the project root directory (parent of chatbot/)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_file = os.path.join(project_root, 'logs', 'chatbot.log')
    
    # Ensure logs directory exists
    log_dir = os.path.dirname(log_file)
    os.makedirs(log_dir, exist_ok=True)
    
    # Configure logging
    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )


def process_webhook_event(event_data):
    """
    Process an incoming event from Google Chat and send a response.
    
    This function extracts key information from the event payload, logs it,
    and sends "Hey, hello" as a response to all messages.
    
    Args:
        event_data (dict): The event payload from Google Chat
        
    Returns:
        dict: A simple acknowledgment response
        
    Raises:
        Exception: If there's an error processing the event
    """
    try:
        # Extract event type
        event_type = event_data.get('type', 'UNKNOWN')
        
        # Extract message information
        message = event_data.get('message', {})
        sender = message.get('sender', {})
        sender_name = sender.get('displayName', 'Unknown')
        message_text = message.get('text', '')
        
        # Get first few words (10-15 words) for the log
        words = message_text.split()
        max_words = 10
        message_preview = ' '.join(words[:max_words])
        
        # Add ellipsis if message was truncated
        if len(words) > max_words:
            message_preview += '...'
        
        # Handle empty messages (e.g., ADDED_TO_SPACE events)
        if not message_preview:
            message_preview = '(no message text)'
        
        # Log the key information
        logging.info(
            f"Event: {event_type} | From: {sender_name} | Message: {message_preview}"
        )
        
        # Send response for MESSAGE events
        if event_type == 'MESSAGE':
            try:
                send_response(event_data)
            except Exception as e:
                logging.error(f"Failed to send response: {e}")
                # Continue even if response fails
        
        # Return a simple acknowledgment
        return {
            "text": "Message received"
        }
        
    except Exception as e:
        # Log the error
        logging.error(f"Error processing event: {e}")
        logging.error(f"Event data: {json.dumps(event_data, indent=2)}")
        raise


def send_response(event_data):
    """
    Send a "Hey, hello" response to the message.
    
    Uses the Chat API with OAuth credentials to post a response
    in the same thread as the original message.
    
    Args:
        event_data (dict): The event payload from Google Chat
    """
    # Extract space and thread information
    space = event_data.get('space', {})
    space_name = space.get('name')
    
    message = event_data.get('message', {})
    thread = message.get('thread', {})
    thread_name = thread.get('name')
    
    if not space_name:
        logging.error("No space name in event data, cannot send response")
        return
    
    try:
        # Get credentials and build service
        creds = get_credentials()
        service = build('chat', 'v1', credentials=creds)
        
        # Prepare the response message
        response_body = {
            'text': 'Hey, hello'
        }
        
        # If there's a thread, reply in that thread
        if thread_name:
            response_body['thread'] = {'name': thread_name}
        
        # Send the message
        result = service.spaces().messages().create(
            parent=space_name,
            body=response_body
        ).execute()
        
        logging.info(f"Response sent successfully to {space_name}")
        
    except HttpError as e:
        logging.error(f"HTTP error sending response: {e}")
        raise
    except Exception as e:
        logging.error(f"Unexpected error sending response: {e}")
        raise


def get_space_info(event_data):
    """
    Extract space information from the Chat event.
    
    Args:
        event_data (dict): The event payload
        
    Returns:
        dict: Space information including name and displayName
    """
    space = event_data.get('space', {})
    return {
        'name': space.get('name', 'Unknown'),
        'displayName': space.get('displayName', 'Unknown Space')
    }


def get_message_info(event_data):
    """
    Extract detailed message information from the Chat event.
    
    Args:
        event_data (dict): The event payload
        
    Returns:
        dict: Message information including sender, text, and timestamp
    """
    message = event_data.get('message', {})
    sender = message.get('sender', {})
    
    return {
        'sender_name': sender.get('displayName', 'Unknown'),
        'sender_email': sender.get('email', 'Unknown'),
        'text': message.get('text', ''),
        'create_time': message.get('createTime', ''),
        'thread_name': message.get('thread', {}).get('name', '')
    }


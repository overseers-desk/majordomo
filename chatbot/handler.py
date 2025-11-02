#!/usr/bin/env python3
"""
Google Spaces Webhook Event Handler

This module processes incoming webhook events from Google Spaces and logs
key information including event type, sender, and message preview.
"""

import logging
import json
import os
from datetime import datetime


def setup_logging():
    """
    Configure logging for the chatbot webhook handler.
    
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
    Process an incoming webhook event from Google Spaces.
    
    This function extracts key information from the webhook payload and logs it.
    Currently, the chatbot does not respond to messages - it only logs them
    for monitoring and debugging purposes.
    
    Args:
        event_data (dict): The webhook event payload from Google Spaces
        
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
        
        # Return a simple acknowledgment
        # Note: Google Spaces webhooks may not require a response,
        # but returning JSON is good practice for CGI scripts
        return {
            "text": "Message received"
        }
        
    except Exception as e:
        # Log the error
        logging.error(f"Error processing webhook event: {e}")
        logging.error(f"Event data: {json.dumps(event_data, indent=2)}")
        raise


def get_space_info(event_data):
    """
    Extract space information from the webhook event.
    
    Args:
        event_data (dict): The webhook event payload
        
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
    Extract detailed message information from the webhook event.
    
    Args:
        event_data (dict): The webhook event payload
        
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


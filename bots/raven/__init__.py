"""
Raven Bot - Package format

Responds to Google Chat events with "hi I'm raven".
Demonstrates package format (vs module format for Tachy).
"""

import logging
import json
import os
from bots import setup_logging, send_response_async


def _load_config():
    """
    Load Raven bot configuration.
    
    Reads config/bots/raven/deepseek.json (even if empty, as placeholder).
    
    Returns:
        dict: Configuration dict (empty if file doesn't exist or is empty)
    """
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    config_file = os.path.join(project_root, 'config', 'bots', 'raven', 'deepseek.json')
    
    config = {}
    
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r') as f:
                content = f.read().strip()
                if content:
                    config = json.loads(content)
        except (json.JSONDecodeError, IOError) as e:
            logging.warning(f"Could not read Raven config from {config_file}: {e}")
    
    return config


def process_event(event_data):
    """
    Process an incoming event from Google Chat and send a response.
    
    Google Chat Apps receive events in a different structure than webhooks.
    The message is wrapped in chat.messagePayload.message.
    
    Args:
        event_data (dict): The event payload from Google Chat
    
    Returns:
        dict: Empty response dict for Google Chat acknowledgment
    """
    try:
        # Load config (placeholder for future use)
        config = _load_config()
        
        # Log raw event data for debugging
        logging.info(f"Raw event data: {json.dumps(event_data)}")
        
        # Google Chat Apps receive events wrapped in 'chat' -> 'messagePayload'
        chat_data = event_data.get('chat', {})
        message_payload = chat_data.get('messagePayload', {})
        message = message_payload.get('message', {})
        
        # If there's a message, this is a MESSAGE event
        if message:
            event_type = 'MESSAGE'
            sender = message.get('sender', {})
            sender_name = sender.get('displayName', 'Unknown')
            message_text = message.get('text', '')
            
            # Get space information
            space = message.get('space', {})
            space_name = space.get('name', '')
            
            # Get thread information
            thread = message.get('thread', {})
            thread_name = thread.get('name', '')
        else:
            # Handle other event types
            event_type = event_data.get('type', 'UNKNOWN')
            sender_name = 'Unknown'
            message_text = ''
            space_name = ''
            thread_name = ''
        
        # Get first few words for the log
        words = message_text.split()
        max_words = 10
        message_preview = ' '.join(words[:max_words])
        
        if len(words) > max_words:
            message_preview += '...'
        
        if not message_preview:
            message_preview = '(no message text)'
        
        # Log the key information
        logging.info(
            f"Event: {event_type} | From: {sender_name} | Message: {message_preview}"
        )
        
        # For MESSAGE events, send response asynchronously via API
        # Return empty response immediately to avoid timeout
        if event_type == 'MESSAGE' and space_name:
            # Send response in background (don't wait for it)
            try:
                send_response_async(space_name, thread_name, "hi I'm raven")
            except Exception as e:
                logging.error(f"Failed to send response: {e}")
        
        # Return immediately - empty response for Chat to acknowledge quickly
        return {}
        
    except Exception as e:
        logging.error(f"Error processing event: {e}")
        logging.error(f"Event data: {json.dumps(event_data, indent=2)}")
        raise


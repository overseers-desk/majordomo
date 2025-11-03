#!/opt/alt/python38/bin/python3.8
"""
CGI Script for Google Chat Events (Pub/Sub Push)

Receives all message events from Google Chat via Pub/Sub push subscription.
Logs all messages and distinguishes between @mentions and regular monitoring.
Uses unified logging from bots package.
"""

import sys
import os
import json
import cgitb
import logging
import base64

# Enable CGI error reporting for debugging
cgitb.enable()

# Add the application directory to the Python path
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)
sys.path.insert(0, parent_dir)
os.chdir(parent_dir)

from bots import setup_logging

# Create logger with source identifier
logger = logging.getLogger('dispatcher')

def is_bot_mentioned(message):
    """Check if the bot was @mentioned in the message."""
    # Check annotations for user mentions
    annotations = message.get('annotations', [])
    for annotation in annotations:
        if annotation.get('type') == 'USER_MENTION':
            user_mention = annotation.get('userMention', {})
            user = user_mention.get('user', {})
            # Check if this is a bot mention
            if user.get('type') == 'BOT':
                return True
    return False


def process_pubsub_event(pubsub_message, subscription='unknown'):
    """Process incoming Pub/Sub message containing Chat event."""
    try:
        # Pub/Sub sends data as base64-encoded JSON
        data = pubsub_message.get('data', '')
        if data:
            decoded = base64.b64decode(data).decode('utf-8')
            event_data = json.loads(decoded)
        else:
            event_data = pubsub_message
        
        # Extract message from event data
        message = event_data.get('message', {})
        
        # Extract available fields
        space_name = message.get('space', {}).get('name', '')
        message_text = message.get('text', '')
        sender_id = message.get('sender', {}).get('name', '')
        
        # Build log with available fields
        log_parts = []
        if space_name:
            log_parts.append(f"Space: {space_name}")
        if sender_id:
            log_parts.append(f"From: {sender_id}")
        if message_text:
            log_parts.append(f"Message: {message_text[:100]}")
        else:
            log_parts.append("Message: (no text)")
        
        logger.info(" | ".join(log_parts) if log_parts else "Event received")
        
    except Exception as e:
        logger.error(f"Error processing event: {e}")
        logger.error(f"Event data: {json.dumps(pubsub_message, indent=2)}")


def main():
    """Main CGI handler for Pub/Sub push events."""
    setup_logging()
    
    # Pub/Sub expects HTTP 200 response
    print("Content-Type: application/json")
    print("Status: 200 OK")
    print()  # End of headers
    
    try:
        # Read request body
        content_length = int(os.environ.get('CONTENT_LENGTH', 0))
        
        if content_length == 0:
            logger.warning("Received request with no content")
            print(json.dumps({}))
            return
        
        request_body = sys.stdin.read(content_length)
        
        # Parse Pub/Sub push request
        push_request = json.loads(request_body)
        
        # Pub/Sub push format: {"message": {...}, "subscription": "..."}
        pubsub_message = push_request.get('message', {})
        subscription = push_request.get('subscription', 'unknown')
        
        if pubsub_message:
            process_pubsub_event(pubsub_message, subscription)
        else:
            logger.warning("No message in Pub/Sub push request")
        
        # Acknowledge receipt
        print(json.dumps({}))
        
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}")
        print(json.dumps({}))
    except Exception as e:
        logger.error(f"Error in main: {e}", exc_info=True)
        print(json.dumps({}))


if __name__ == '__main__':
    main()

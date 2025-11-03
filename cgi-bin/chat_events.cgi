#!/opt/alt/python38/bin/python3.8
"""
CGI Script for Google Chat Events (Pub/Sub Push)

Receives all message events from Google Chat via Pub/Sub push subscription.
Logs all messages and distinguishes between @mentions and regular monitoring.
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


def setup_logging():
    """
    Configure logging for events.
    
    Logging location priority:
    1. If LOG_DIR environment variable is set, use that directory
    2. If ../logs directory exists (relative to public_html), use that
    3. Otherwise, log to console only (stderr)
    """
    log_dir = None
    log_file = None
    
    # Check for environment variable first
    if os.environ.get('LOG_DIR'):
        log_dir = os.environ.get('LOG_DIR')
        if os.path.isdir(log_dir):
            log_file = os.path.join(log_dir, 'events.log')
    
    # If no env var, check for ../logs directory (parent of public_html)
    if not log_file:
        parent_logs = os.path.join(os.path.dirname(parent_dir), 'logs')
        if os.path.isdir(parent_logs):
            log_dir = parent_logs
            log_file = os.path.join(parent_logs, 'events.log')
    
    # Configure logging
    if log_file:
        logging.basicConfig(
            filename=log_file,
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
    else:
        # Log to console only
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
            stream=sys.stderr
        )


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


def process_pubsub_event(pubsub_message):
    """Process incoming Pub/Sub message containing Chat event."""
    try:
        # Pub/Sub sends data as base64-encoded JSON
        data = pubsub_message.get('data', '')
        if data:
            decoded = base64.b64decode(data).decode('utf-8')
            event_data = json.loads(decoded)
        else:
            event_data = pubsub_message
        
        # Extract message from event
        message = event_data.get('message', {})
        if not message:
            logging.warning("No message in event data")
            return
        
        # Extract key information
        sender = message.get('sender', {})
        sender_name = sender.get('displayName', 'Unknown')
        message_text = message.get('text', '')
        
        space = message.get('space', {})
        space_display = space.get('displayName', 'Unknown Space')
        
        # Check if bot was mentioned
        mentioned = is_bot_mentioned(message)
        
        # Log the message
        logging.info(
            f"Space: {space_display} | From: {sender_name} | "
            f"Bot mentioned: {mentioned} | Message: {message_text[:100]}"
        )
        
    except Exception as e:
        logging.error(f"Error processing event: {e}")
        logging.error(f"Event data: {json.dumps(pubsub_message, indent=2)}")


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
            logging.warning("Received request with no content")
            print(json.dumps({}))
            return
        
        request_body = sys.stdin.read(content_length)
        
        # Parse Pub/Sub push request
        push_request = json.loads(request_body)
        
        # Pub/Sub push format: {"message": {...}, "subscription": "..."}
        pubsub_message = push_request.get('message', {})
        
        if pubsub_message:
            process_pubsub_event(pubsub_message)
        else:
            logging.warning("No message in Pub/Sub push request")
        
        # Acknowledge receipt
        print(json.dumps({}))
        
    except json.JSONDecodeError as e:
        logging.error(f"JSON decode error: {e}")
        print(json.dumps({}))
    except Exception as e:
        logging.error(f"Error in main: {e}", exc_info=True)
        print(json.dumps({}))


if __name__ == '__main__':
    main()


#!/opt/alt/python38/bin/python3.8
"""
CGI Script for Google Chat App

This script receives events from Google Chat and processes them
through the chatbot handler, which logs messages and sends responses.

For CGI deployment, this file should be:
1. Placed in your web server's cgi-bin directory (or configured CGI location)
2. Made executable (chmod +x google_chat_app.cgi)
3. Have correct shebang pointing to your Python 3 interpreter
"""

import sys
import os
import json
import cgitb
import logging

# Enable CGI error reporting for debugging
cgitb.enable()

# Add the application directory to the Python path
# This assumes google_chat_app.cgi is in cgi-bin/ subdirectory and chatbot module is in parent directory
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)
sys.path.insert(0, parent_dir)
os.chdir(parent_dir)

# Import the chatbot handler
from chatbot.handler import setup_logging, process_webhook_event


def main():
    """
    Main CGI handler function.
    
    Receives event POST requests from Google Chat, processes them,
    and returns appropriate responses.
    """
    # Setup logging
    setup_logging()
    
    # Set response headers
    print("Content-Type: application/json")
    print("Status: 200 OK")
    print()  # End of headers - required blank line
    
    try:
        # Read request body length from environment
        content_length = int(os.environ.get('CONTENT_LENGTH', 0))
        
        # Check if we received any data
        if content_length == 0:
            logging.warning("Received request with no content")
            response = {"error": "No data received"}
            print(json.dumps(response))
            return
        
        # Read the POST data from stdin
        request_body = sys.stdin.read(content_length)
        
        # Parse JSON payload
        try:
            event_data = json.loads(request_body)
        except json.JSONDecodeError as e:
            logging.error(f"JSON decode error: {e}")
            logging.error(f"Request body: {request_body}")
            response = {"error": "Invalid JSON payload"}
            print(json.dumps(response))
            return
        
        # Process the event through the chatbot handler
        response = process_webhook_event(event_data)
        
        # Send response back to Google Chat
        print(json.dumps(response))
        
    except Exception as e:
        # Log any unexpected errors
        logging.error(f"Error processing event: {e}", exc_info=True)
        
        # Return error response
        response = {"error": "Internal server error"}
        print(json.dumps(response))


if __name__ == '__main__':
    main()


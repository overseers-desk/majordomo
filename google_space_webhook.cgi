#!/opt/alt/python38/bin/python3.8
"""
CGI Script for Google Spaces Webhook Dispatcher

This script receives webhook events from Google Spaces and dispatches
them to the chatbot handler for processing.

For CGI deployment, this file should be:
1. Placed in your web server's cgi-bin directory (or configured CGI location)
2. Made executable (chmod +x google_space_webhook.cgi)
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
# This assumes google_space_webhook.cgi is in the same directory as the chatbot module
sys.path.insert(0, os.path.dirname(__file__))

# Import the chatbot handler
from chatbot.handler import setup_logging, process_webhook_event


def main():
    """
    Main CGI handler function.
    
    Receives webhook POST requests from Google Spaces, processes them,
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
        
        # Process the webhook event through the chatbot handler
        response = process_webhook_event(event_data)
        
        # Send response back to Google Spaces
        print(json.dumps(response))
        
    except Exception as e:
        # Log any unexpected errors
        logging.error(f"Error processing webhook: {e}", exc_info=True)
        
        # Return error response
        response = {"error": "Internal server error"}
        print(json.dumps(response))


if __name__ == '__main__':
    main()


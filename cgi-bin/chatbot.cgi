#!/opt/alt/python38/bin/python3.8
"""
CGI Script for Chat Bot (Multi-Bot Router)

This script receives events from Google Chat and routes them to the appropriate bot
based on the path (e.g., /chatbot.cgi/tachy or /chatbot.cgi/raven).
"""

import sys
import os
import json
import cgitb
import logging

# Enable CGI error reporting for debugging
cgitb.enable()

# Add the application directory to the Python path
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)
sys.path.insert(0, parent_dir)
os.chdir(parent_dir)

from bots import setup_logging, load_bot


def main():
    """
    Main CGI handler function.
    
    Receives event POST requests from Google Chat, routes to appropriate bot,
    and returns appropriate responses.
    """
    # Setup logging
    setup_logging()
    
    # Set response headers
    print("Content-Type: application/json")
    print("Status: 200 OK")
    print()  # End of headers - required blank line
    
    try:
        # Extract bot name from PATH_INFO
        # Examples: /chatbot.cgi/tachy -> PATH_INFO = '/tachy'
        #           /chatbot.cgi/raven -> PATH_INFO = '/raven'
        path_info = os.environ.get('PATH_INFO', '')
        bot_name = path_info.strip('/').split('/')[0] if path_info else 'tachy'
        
        # Default to 'tachy' if no path or empty path
        if not bot_name:
            bot_name = 'tachy'
        
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
        
        # Load the appropriate bot
        try:
            bot = load_bot(bot_name)
        except (ImportError, AttributeError) as e:
            logging.error(f"Failed to load bot '{bot_name}': {e}")
            response = {"error": f"Bot '{bot_name}' not found"}
            print(json.dumps(response))
            return
        
        # Process the event through the bot
        response = bot.process_event(event_data)
        
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


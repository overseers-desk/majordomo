"""
Raven Bot - Package format

AI-powered bot that answers questions about the Google-Spaces-Tasks-reporter repository.
Uses DeepSeek API to provide intelligent responses based on repo documentation and code.
"""

import logging
import json
import os
import requests
from bots import setup_logging, send_response_async

# Create logger with source identifier
logger = logging.getLogger('bot raven')


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
            logger.warning(f"Could not read Raven config from {config_file}: {e}")
    
    return config


def _get_repo_context():
    """
    Get repository context by reading key documentation files.
    
    Returns:
        str: Concatenated content from README and documentation files
    """
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    context = []
    
    # Files to include in context
    context_files = [
        'README.md',
        'docs/CHATBOT-SETUP.md',
        'WEB_APPLICATION.md',
        'GOOGLE_CHAT_TASKS_LIMITATIONS.md'
    ]
    
    for filename in context_files:
        filepath = os.path.join(project_root, filename)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()[:3000]  # First 3000 chars of each file
                context.append(f"=== {filename} ===\n{content}\n")
        except Exception as e:
            logger.debug(f"Could not read {filename}: {e}")
    
    return "\n".join(context)


def _ask_deepseek(question, config):
    """
    Send a question to DeepSeek API and get an AI response.
    
    Args:
        question (str): The user's question
        config (dict): DeepSeek configuration with API key
    
    Returns:
        str: AI response or error message
    """
    if not config.get('api_key'):
        return "⚠️ DeepSeek API key not configured. Please add it to config/bots/raven/deepseek.json"
    
    api_base = config.get('api_base', 'https://api.deepseek.com')
    model = config.get('model', 'deepseek-chat')
    
    url = f"{api_base}/chat/completions"
    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json"
    }
    
    # Get repository context
    context = _get_repo_context()
    
    # Build the prompt
    system_prompt = """You are Raven, a helpful AI assistant for the Google-Spaces-Tasks-reporter project. 
You answer questions about the codebase, setup, configuration, and functionality.
Provide concise, accurate answers based on the repository documentation.
If you're unsure, say so rather than making up information."""
    
    user_message = f"Repository Context:\n{context}\n\nUser Question: {question}\n\nPlease provide a helpful answer."
    
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ],
        "temperature": 0.7,
        "max_tokens": 800
    }
    
    try:
        logger.info(f"Sending question to DeepSeek: {question[:100]}...")
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        
        result = response.json()
        answer = result['choices'][0]['message']['content']
        
        logger.info(f"DeepSeek response received. Tokens: {result.get('usage', {})}")
        return answer
        
    except requests.exceptions.Timeout:
        logger.error("DeepSeek API timeout")
        return "⏱️ Sorry, the request timed out. Please try again."
    except requests.exceptions.RequestException as e:
        logger.error(f"DeepSeek API error: {e}")
        return f"❌ Error connecting to AI service. Please try again later."
    except Exception as e:
        logger.error(f"Unexpected error calling DeepSeek: {e}")
        return "❌ An unexpected error occurred. Please try again."


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
        
        # Debug: Log the full event structure
        try:
            event_json = json.dumps(event_data, indent=2)
            logger.info(f"DEBUG - Received event structure: {event_json}")
        except Exception as e:
            logger.error(f"Failed to serialize event data: {e}")
            logger.info(f"DEBUG - Event data keys: {list(event_data.keys())}")
        
        # Google Chat Apps receive events in different structures
        # Try the new structure first (direct message object)
        message = event_data.get('message', {})
        
        # If not found, try the old structure (chat.messagePayload.message)
        if not message:
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
        logger.info(
            f"Event: {event_type} | From: {sender_name} | Message: {message_preview}"
        )
        
        # For MESSAGE events, send response asynchronously via API
        # Return empty response immediately to avoid timeout
        if event_type == 'MESSAGE' and space_name:
            # Send response in background (don't wait for it)
            try:
                # Remove bot mention from message text to get the actual question
                # Google Chat includes the bot mention in the text
                question = message_text.strip()
                
                # Remove common bot mention patterns
                question = question.replace('@Raven', '').replace('Raven', '').strip()
                
                if not question or question.lower() in ['hi', 'hello', 'hey']:
                    # Simple greeting - use default response
                    response_text = "Hi! I'm Raven, your AI assistant for this repository. Ask me anything about the Google-Spaces-Tasks-reporter codebase!"
                else:
                    # Use DeepSeek to answer the question
                    logger.info(f"Processing question from {sender_name}: {question[:100]}")
                    response_text = _ask_deepseek(question, config)
                
                send_response_async(space_name, thread_name, response_text, bot_name='raven')
            except Exception as e:
                logger.error(f"Failed to send response: {e}")
        
        # Return immediately - empty response for Chat to acknowledge quickly
        return {}
        
    except Exception as e:
        logger.error(f"Error processing event: {e}")
        logger.error(f"Event data: {json.dumps(event_data, indent=2)}")
        raise


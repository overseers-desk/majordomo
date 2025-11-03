# Google Chat App Handler

## Overview

This module handles incoming events from Google Chat. The Chat App receives messages where it is @mentioned, logs key information, and responds with "Hey, hello".

## Prerequisites

Before using the Chat App, you need to configure it in Google Cloud Console:

### Google Cloud Console Configuration

1. Navigate to the [Google Cloud Console](https://console.cloud.google.com/) > APIs & Services > Chat API > Configuration
2. Configure the Chat app:
   - **App name**: "Google Spaces Tasks Assistant" (or your preferred name)
   - **Avatar URL**: (optional)
   - **Description**: "Task tracking and chat assistant"
   - **Interactive features**: 
     - ✅ Enable this option
     - **HTTP endpoint URL**: `https://example.com/cgi-bin/google_chat_app.cgi`
   - **Functionality**: 
     - ✅ Enable "Join spaces and group conversations"
   - **Permissions**: Verify these scopes are present (should already exist from OAuth setup):
     - `https://www.googleapis.com/auth/chat.spaces`
     - `https://www.googleapis.com/auth/chat.messages`
     - `https://www.googleapis.com/auth/chat.messages.readonly`
3. **Visibility**: 
   - Select "Make this chat app available to specific people and groups in your domain"
   - Add email addresses of users who should be able to install the app
4. Save the configuration

### Installing the App in a Space

After Cloud Console configuration:

1. Go to your Google Chat space
2. Click the space name at the top
3. Select "Apps & integrations" → "Add apps"
4. Your app should now appear in the list
5. Click to add it to the space

The app will start receiving messages and responding immediately.

## Architecture

The Chat App system consists of two main components:

1. **`google_chat_app.cgi`** (top-level): CGI dispatcher that receives HTTP POST requests from Google Chat
2. **`chatbot/handler.py`**: Python module that processes Chat events, logs information, and sends responses

### How It Works

1. Google Chat sends an event POST request when a message is posted
2. The CGI script (`google_chat_app.cgi`) receives the request
3. The request is forwarded to `chatbot.handler.process_webhook_event()`
4. Key information is logged: event type, sender name, and message preview
5. A "Hey, hello" response is sent back using the Chat API
6. An acknowledgment response is sent back to Google Chat

## Current Functionality

The Chat App logs messages where it is @mentioned and responds with "Hey, hello" to those messages.

### What Gets Logged

Each incoming message logs:
- **Event type**: MESSAGE, ADDED_TO_SPACE, etc.
- **Sender name**: Display name of the person who posted
- **Message preview**: First 10 words of the message

### What Gets Responded

For MESSAGE events (when @mentioned):
- Sends "Hey, hello" as a response
- Posts in the same thread to maintain conversation context
- Uses OAuth credentials to authenticate with Chat API

### Log Format

```
2025-11-02 12:00:00 - INFO - Event: MESSAGE | From: John Doe | Message: Hello team, I wanted to discuss...
2025-11-02 12:00:01 - INFO - Response sent successfully to spaces/ABC123
2025-11-02 12:01:15 - INFO - Event: MESSAGE | From: Jane Smith | Message: Quick question about...
2025-11-02 12:01:16 - INFO - Response sent successfully to spaces/ABC123
2025-11-02 12:02:30 - INFO - Event: ADDED_TO_SPACE | From: Unknown | Message: (no message text)
```

Logs are written to: `logs/chatbot.log`

## Testing

### Local Testing with curl

The easiest way to test is using the provided test script:

```bash
chmod +x test_chat_app.sh
./test_chat_app.sh
```

This script sends several test payloads to the Chat App endpoint and verifies the system is working.

### Manual Testing

You can also send individual test requests:

```bash
curl -X POST http://localhost/google_chat_app.cgi \
  -H "Content-Type: application/json" \
  -d '{
    "type": "MESSAGE",
    "message": {
      "sender": {"displayName": "Your Name"},
      "text": "Test message from manual curl",
      "createTime": "2025-11-02T12:00:00.000Z"
    }
  }'
```

### Testing with Python

You can also test the handler directly from Python:

```python
import sys
sys.path.insert(0, '..')
from chatbot.handler import setup_logging, process_webhook_event

setup_logging()

# Test payload
event_data = {
    "type": "MESSAGE",
    "message": {
        "sender": {"displayName": "Test User"},
        "text": "This is a test message",
        "createTime": "2025-11-02T12:00:00.000Z"
    }
}

response = process_webhook_event(event_data)
print(response)
```

### Verify Logs

After testing, check the log file:

```bash
tail -f logs/chatbot.log
```

Or view all logs:

```bash
cat logs/chatbot.log
```

## Testing

### Testing the Chat App in Google Chat

After configuration and deployment:

1. Post a message in the space where the app is installed
2. The app should respond with "Hey, hello"
3. Check `logs/chatbot.log` for the logged entry and response confirmation
4. You should see entries like:
```
2025-11-02 14:30:15 - INFO - Event: MESSAGE | From: Alice | Message: Testing the new app...
2025-11-02 14:30:16 - INFO - Response sent successfully to spaces/ABC123
```

## Event Payload Structure

Google Chat sends events with the following structure:

### MESSAGE Event
```json
{
  "type": "MESSAGE",
  "message": {
    "sender": {
      "displayName": "John Doe",
      "email": "john@example.com"
    },
    "text": "Message content here",
    "createTime": "2025-11-02T12:00:00.000Z",
    "space": {
      "name": "spaces/AAAA1234",
      "displayName": "Team Space"
    },
    "thread": {
      "name": "spaces/AAAA1234/threads/THREAD123"
    }
  },
  "space": {
    "name": "spaces/AAAA1234",
    "displayName": "Team Space"
  }
}
```

### ADDED_TO_SPACE Event
```json
{
  "type": "ADDED_TO_SPACE",
  "space": {
    "name": "spaces/AAAA5678",
    "displayName": "New Space"
  },
  "user": {
    "displayName": "Bot Admin",
    "email": "admin@example.com"
  }
}
```

## Troubleshooting

### No Logs Appearing

1. Check that the CGI script is executable:
   ```bash
   ls -l google_chat_app.cgi
   # Should show: -rwxr-xr-x
   ```

2. Verify the logs directory exists and is writable:
   ```bash
   ls -ld logs/
   # Should be writable by the web server user
   ```

3. Check web server error logs for CGI errors

### No Response in Chat

1. Verify OAuth credentials exist:
   ```bash
   ls -la config/token.json config/client_secret.json
   ```

2. Check the log file for API errors:
   ```bash
   grep "error" logs/chatbot.log
   ```

3. Ensure the Chat API scopes include `chat.messages`

### JSON Decode Errors

If you see JSON decode errors in the logs, the payload may be malformed. Check:
- Content-Type header is `application/json`
- Payload is valid JSON (use a JSON validator)
- Request body is not empty

### Permission Errors

If the script can't write to the log file:

```bash
# Ensure logs directory is writable
chmod 755 logs/
touch logs/chatbot.log
chmod 644 logs/chatbot.log
```

## Development Roadmap

Future enhancements:

- **Intent recognition**: Parse messages to understand user intent
- **Context-aware responses**: Reply based on message content
- **Task integration**: Connect with the existing task reporter functionality
- **Request verification**: Verify requests are from Google Chat
- **Rate limiting**: Prevent abuse
- **Rich formatting**: Use cards and interactive elements

## Files

- `chatbot/__init__.py`: Module initialisation
- `chatbot/handler.py`: Event processing, logging, and response sending
- `google_chat_app.cgi`: Top-level CGI dispatcher
- `test_chat_app.sh`: Test script for local verification
- `logs/chatbot.log`: Log file (created automatically)


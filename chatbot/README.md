# Chatbot Module

## Overview

This module handles incoming webhook events from Google Spaces. The chatbot dispatcher receives messages posted in Google Spaces, logs key information, and provides a foundation for future chatbot response functionality.

## Architecture

The webhook system consists of two main components:

1. **`google_space_webhook.cgi`** (top-level): CGI dispatcher that receives HTTP POST requests from Google Spaces
2. **`chatbot/handler.py`**: Python module that processes webhook events and logs information

### How It Works

1. Google Spaces sends a webhook POST request when a message is posted
2. The CGI script (`google_space_webhook.cgi`) receives the request
3. The request is forwarded to `chatbot.handler.process_webhook_event()`
4. Key information is logged: event type, sender name, and message preview
5. An acknowledgment response is sent back to Google Spaces

## Current Functionality

The chatbot currently **logs only** and does not respond to messages. This is intentional as we're building in small steps.

### What Gets Logged

Each incoming message logs:
- **Event type**: MESSAGE, ADDED_TO_SPACE, etc.
- **Sender name**: Display name of the person who posted
- **Message preview**: First 10 words of the message

### Log Format

```
2025-11-02 12:00:00 - INFO - Event: MESSAGE | From: John Doe | Message: Hello team, I wanted to discuss...
2025-11-02 12:01:15 - INFO - Event: MESSAGE | From: Jane Smith | Message: Quick question about...
2025-11-02 12:02:30 - INFO - Event: ADDED_TO_SPACE | From: Unknown | Message: (no message text)
```

Logs are written to: `logs/chatbot.log`

## Testing

### Local Testing with curl

The easiest way to test is using the provided test script:

```bash
chmod +x test_webhook.sh
./test_webhook.sh
```

This script sends several test payloads to the webhook endpoint and verifies the system is working.

### Manual Testing

You can also send individual test requests:

```bash
curl -X POST http://localhost/google_space_webhook.cgi \
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

## Google Spaces Configuration

To connect your Google Space to this webhook:

1. Open your Google Space
2. Click the space name at the top
3. Select "Apps & integrations"
4. Click "Manage webhooks"
5. Click "Add webhook"
6. Provide:
   - **Name**: Choose a descriptive name (e.g., "Chatbot Dispatcher")
   - **Avatar URL**: (optional)
   - **Webhook URL**: `https://your-server.com/google_space_webhook.cgi`
7. Click "Save"

### Testing the Google Spaces Integration

After configuration:

1. Post a message in the Space
2. Check `logs/chatbot.log` for the logged entry
3. You should see an entry with your message preview

Example:
```
2025-11-02 14:30:15 - INFO - Event: MESSAGE | From: Alice | Message: Testing the new webhook integration...
```

## Webhook Payload Structure

Google Spaces sends webhooks with the following structure:

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
   ls -l google_space_webhook.cgi
   # Should show: -rwxr-xr-x
   ```

2. Verify the logs directory exists and is writable:
   ```bash
   ls -ld logs/
   # Should be writable by the web server user
   ```

3. Check web server error logs for CGI errors

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

Future enhancements (not yet implemented):

- **Message parsing**: Extract intent and entities from messages
- **Response generation**: Reply to messages based on content
- **Task integration**: Connect with the existing task reporter functionality
- **Authentication**: Verify webhook requests are from Google
- **Rate limiting**: Prevent abuse
- **Message threading**: Maintain conversation context

## Files

- `chatbot/__init__.py`: Module initialisation
- `chatbot/handler.py`: Webhook event processing and logging
- `google_space_webhook.cgi`: Top-level CGI dispatcher
- `test_webhook.sh`: Test script for local verification
- `logs/chatbot.log`: Log file (created automatically)


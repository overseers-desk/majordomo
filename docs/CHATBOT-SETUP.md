# Multi-Bot Chat App Setup

## Overview

This system supports multiple bots (Tachy and Orcal) that respond to Google Chat messages when @mentioned. Each bot has its own identity and response message:
- **Tachy**: Responds with "hi I'm techy"
- **Orcal**: Responds with "hi I'm orcal"

Both bots use path-based routing via `chatbot.cgi` and share unified logging.

## Prerequisites

Before using the Chat Apps, you need to configure each bot in Google Cloud Console:

### Google Cloud Console Configuration

#### For Tachy Bot:

1. Navigate to the [Google Cloud Console](https://console.cloud.google.com/) > APIs & Services > Chat API > Configuration
2. Create or edit Chat App "Tachy":
   - **App name**: "Tachy" (or your preferred name)
   - **Avatar URL**: (optional)
   - **Description**: "Tachy bot assistant"
   - **Interactive features**: 
     - ✅ Enable this option
     - **HTTP endpoint URL**: `https://example.com/cgi-bin/chatbot.cgi/tachy`
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

#### For Orcal Bot:

1. In the same Google Cloud Console > APIs & Services > Chat API > Configuration
2. Create a **separate** Chat App "Orcal":
   - **App name**: "Orcal" (or your preferred name)
   - **Avatar URL**: (optional)
   - **Description**: "Orcal bot assistant"
   - **Interactive features**: 
     - ✅ Enable this option
     - **HTTP endpoint URL**: `https://example.com/cgi-bin/chatbot.cgi/orcal`
   - **Functionality**: 
     - ✅ Enable "Join spaces and group conversations"
   - **Permissions**: Verify these scopes are present (same as Tachy)
3. **Visibility**: 
   - Select "Make this chat app available to specific people and groups in your domain"
   - Add email addresses of users who should be able to install the app
4. Save the configuration

### Installing the Apps in a Space

After Cloud Console configuration:

1. Go to your Google Chat space
2. Click the space name at the top
3. Select "Apps & integrations" → "Add apps"
4. Both "Tachy" and "Orcal" should now appear in the list
5. Click to add each bot to the space as needed

The bots will start receiving messages and responding immediately when @mentioned.

## Architecture

The multi-bot system consists of:

1. **`chatbot.cgi`**: CGI dispatcher that receives HTTP POST requests from Google Chat and routes to appropriate bot based on path (`/tachy` or `/orcal`)
2. **`bots/__init__.py`**: Common utilities, unified logging, and bot loader
3. **`bots/tachy.py`**: Tachy bot (module format) - responds "hi I'm techy"
4. **`bots/orcal/__init__.py`**: Orcal bot (package format) - responds "hi I'm orcal"

### How It Works

1. Google Chat sends an event POST request when a message is posted where a bot is @mentioned
2. The request URL includes the bot path (e.g., `/chatbot.cgi/tachy` or `/chatbot.cgi/orcal`)
3. The CGI script (`chatbot.cgi`) extracts the bot name from the path
4. The bot loader (`bots.load_bot()`) dynamically loads the appropriate bot module/package
5. The request is forwarded to `bot.process_event(event_data)`
6. Key information is logged: event type, sender name, and message preview
7. A bot-specific response is sent back using the Chat API
8. An acknowledgment response is sent back to Google Chat

## Current Functionality

Each bot logs messages where it is @mentioned and responds with its unique message:
- **Tachy**: "hi I'm techy"
- **Orcal**: "hi I'm orcal"

### What Gets Logged

Each incoming message logs:
- **Event type**: MESSAGE, ADDED_TO_SPACE, etc.
- **Sender name**: Display name of the person who posted
- **Message preview**: First 10 words of the message

### What Gets Responded

For MESSAGE events (when @mentioned):
- Tachy sends "hi I'm techy" as a response
- Orcal sends "hi I'm orcal" as a response
- Posts in the same thread to maintain conversation context
- Uses OAuth credentials to authenticate with Chat API

### Log Format

```
2025-11-02 12:00:00 - INFO - Event: MESSAGE | From: John Doe | Message: Hello team, I wanted to discuss...
2025-11-02 12:00:01 - INFO - Response sent successfully to spaces/ABC123: hi I'm techy
2025-11-02 12:01:15 - INFO - Event: MESSAGE | From: Jane Smith | Message: Quick question about...
2025-11-02 12:01:16 - INFO - Response sent successfully to spaces/ABC123: hi I'm orcal
2025-11-02 12:02:30 - INFO - Event: ADDED_TO_SPACE | From: Unknown | Message: (no message text)
```

Logs are written to: `../logs/google-chatbot.log` (when no LOG_DIR environment variable)

## Testing

### Local Testing with curl

Test Tachy bot:
```bash
curl -X POST http://localhost/cgi-bin/chatbot.cgi/tachy \
  -H "Content-Type: application/json" \
  -d '{
    "type": "MESSAGE",
    "chat": {
      "messagePayload": {
        "message": {
          "sender": {"displayName": "Test User"},
          "text": "Test message for Tachy",
          "space": {"name": "spaces/ABC123"},
          "thread": {"name": "spaces/ABC123/threads/THREAD123"}
        }
      }
    }
  }'
```

Test Orcal bot:
```bash
curl -X POST http://localhost/cgi-bin/chatbot.cgi/orcal \
  -H "Content-Type: application/json" \
  -d '{
    "type": "MESSAGE",
    "chat": {
      "messagePayload": {
        "message": {
          "sender": {"displayName": "Test User"},
          "text": "Test message for Orcal",
          "space": {"name": "spaces/ABC123"},
          "thread": {"name": "spaces/ABC123/threads/THREAD123"}
        }
      }
    }
  }'
```

### Testing with Python

You can also test the bots directly from Python:

```python
import sys
sys.path.insert(0, '.')
from bots import setup_logging, load_bot

setup_logging()

# Test Tachy
tachy = load_bot('tachy')
event_data = {
    "type": "MESSAGE",
    "chat": {
        "messagePayload": {
            "message": {
                "sender": {"displayName": "Test User"},
                "text": "This is a test message",
                "space": {"name": "spaces/ABC123"},
                "thread": {"name": "spaces/ABC123/threads/THREAD123"}
            }
        }
    }
}
response = tachy.process_event(event_data)
print(response)
```

### Verify Logs

After testing, check the log file:

```bash
tail -f ../logs/google-chatbot.log
```

## Event Payload Structure

Google Chat sends events with the following structure:

### MESSAGE Event
```json
{
  "type": "MESSAGE",
  "chat": {
    "messagePayload": {
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
      }
    }
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
   ls -l cgi-bin/chatbot.cgi
   # Should show: -rwxr-xr-x
   ```

2. Verify the logs directory exists and is writable:
   ```bash
   ls -ld ../logs/
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
   grep "error" ../logs/google-chatbot.log
   ```

3. Ensure the Chat API scopes include `chat.messages`

4. Verify the correct callback URL is configured in Google Cloud Console:
   - Tachy: `https://example.com/cgi-bin/chatbot.cgi/tachy`
   - Orcal: `https://example.com/cgi-bin/chatbot.cgi/orcal`

### Wrong Bot Responding

- Check that each bot has a separate Chat App configuration in Google Cloud Console
- Verify each bot's callback URL includes the correct path (`/tachy` or `/orcal`)
- Ensure you're @mentioning the correct bot in the space

### JSON Decode Errors

If you see JSON decode errors in the logs, the payload may be malformed. Check:
- Content-Type header is `application/json`
- Payload is valid JSON (use a JSON validator)
- Request body is not empty

### Permission Errors

If the script can't write to the log file:

```bash
# Ensure logs directory is writable
mkdir -p ../logs
chmod 755 ../logs/
touch ../logs/google-chatbot.log
chmod 644 ../logs/google-chatbot.log
```

## Files

- `bots/__init__.py`: Common utilities, unified logging, and bot loader
- `bots/tachy.py`: Tachy bot (module format)
- `bots/orcal/__init__.py`: Orcal bot (package format)
- `chatbot.cgi`: Multi-bot CGI dispatcher
- `../logs/google-chatbot.log`: Unified log file (created automatically)


# Google Workspace Events API Setup - Monitor All Messages

This document describes how to set up the Google Workspace Events API integration to receive **all Google Chat messages** (not just @mentions) via Push subscription.

**Use Case**: This setup is for scenarios where you need to monitor all messages in a space proactively, such as:
- Task tracking and compliance monitoring
- Automated logging and analytics
- Context-aware bot responses based on conversation flow

**Note**: If you only need the bots to respond when @mentioned, you don't need this setup - the standard bot configuration (`chatbot.cgi`) already handles that.

## Architecture

**Push Subscription Model:**
```
Google Chat → Events API → Pub/Sub Topic → Push to → dispatcher.cgi → Logs
```

No daemon needed - Pub/Sub pushes events to your existing CGI infrastructure (just like the @mention bots).

## Prerequisites & Infrastructure Setup

### Step 1: Google Cloud Console Configuration

1. Navigate to [Google Cloud Console](https://console.cloud.google.com) for project `project-y-433100` (or your configured project)

2. Enable Google Workspace Events API:
   - Go to "APIs & Services" > "Library"
   - Search for: "Google Workspace Events API"
   - Click on it and verify it shows "Status: Enabled"
   - Service name should be: `workspaceevents.googleapis.com`

3. Enable Cloud Pub/Sub API (SEPARATE API):
   - Still in "APIs & Services" > "Library"
   - Search for: "Cloud Pub/Sub API" or just "Pub/Sub"
   - Click on "Cloud Pub/Sub API" in the results
   - Service name should be: `pubsub.googleapis.com`
   - Click "Enable" if not already enabled

### Step 2: Configure Push Subscription

Using Google Cloud Console:

1. Navigate to "Pub/Sub" > "Topics"
2. Verify or create topic: `chat-message-events`
3. Click on the topic
4. **Grant Google Workspace permission to publish** (CRITICAL STEP):
   - Click the "SHOW INFO PANEL" button on the right side of the screen
   - In the info panel, find the "Permissions" section
   - Click "ADD PRINCIPAL"
   - Add principal: `chat-api-push@system.gserviceaccount.com`
   - Role: **Pub/Sub Publisher**
   - Click "Save"
   - Note: This is the Google Workspace service account that publishes events to your topic

5. Go to "Subscriptions" tab
6. Configure Push subscription:

**Option A: Edit existing subscription**
   - Click on existing subscription (e.g., `chat-message-events-sub`)
   - Click "Edit" at the top
   - Change "Delivery type" from "Pull" to "Push"
   - Set "Endpoint URL" to: `https://example.com/cgi-bin/dispatcher.cgi`
   - Leave "Enable authentication" unchecked for now (can add later)
   - Click "Update"

**Option B: Create new Push subscription**
   - Click "Create Subscription"
   - Subscription ID: `chat-message-events-push`
   - Select topic: `chat-message-events`
   - Delivery type: **Push**
   - Endpoint URL: `https://example.com/cgi-bin/dispatcher.cgi`
   - Leave authentication disabled for now
   - Click "Create"

### Step 3: Verify OAuth Credentials

Note: For Push subscriptions, you don't need a service account for receiving events - Pub/Sub pushes TO your CGI endpoint.

You only need OAuth credentials (already in `config/token.json`) for creating Workspace Events API subscriptions, which you already have from the existing bot setup.

## Deployment Instructions

### Step 1: Verify CGI Script

The CGI script is already created and executable:
```bash
ls -l cgi-bin/dispatcher.cgi
# Should show: -rwxr-xr-x (executable)
```

### Step 2: Create Workspace Events Subscription

For each Google Chat space you want to monitor:

```bash
# List available spaces first
python3 google_chat_reporter.py spaces

# Create subscription for a space (using dispatcher module)
python3 -m dispatcher --space spaces/YOUR_SPACE_ID
```

Or with explicit project/topic:
```bash
python3 -m dispatcher --space spaces/YOUR_SPACE_ID --project project-y-433100 --topic chat-message-events
```

This creates a Workspace Events API subscription that tells Google Chat to send all message events from that space to your Pub/Sub topic, which then pushes them to your CGI endpoint.

### Step 3: Test the Integration

1. Post a message in the subscribed space (without @mentioning any bot)
2. Check the unified log file:
   ```bash
   tail -f ../logs/google-chatbot.log
   ```
3. You should see:
   ```
   2025-11-02 15:30:00 - INFO - Space: Team Space | From: John Doe | Bot mentioned: False | Message: Testing the event system
   ```

4. Now @mention a bot in a message:
   ```
   @Tachy hello
   ```
5. Check the unified log:
   ```bash
   tail -f ../logs/google-chatbot.log    # All events and bot responses in one file
   ```

### Step 4: Verify Everything Works

Expected behaviour:

- **Regular messages** (no @mention):
  - Logged in `../logs/google-chatbot.log` with "Bot mentioned: False"
  - NOT sent to bot endpoints (bots only respond when @mentioned)
  
- **@mention messages**:
  - Logged in `../logs/google-chatbot.log` with "Bot mentioned: True"
  - ALSO processed by the bot endpoint (bot responds with its message)
  - Bot response is also logged in the same file

## File Structure

```
├── cgi-bin/
│   ├── chatbot.cgi           (Multi-bot router for @mention bots)
│   └── dispatcher.cgi       (Receives all messages via Pub/Sub)
├── dispatcher/
│   └── __init__.py          (CLI utilities for creating subscriptions)
├── config/
│   ├── dispatcher.json       (Dispatcher config: project_id, topic_name)
│   └── token.json           (OAuth credentials)
└── [parent directory]/
    └── logs/
        └── google-chatbot.log    (Unified log for all components)
```

## Usage

### List Active Subscriptions

```bash
python3 -m dispatcher --list
```

### Create Subscription for Additional Spaces

```bash
# Get space ID from google_chat_reporter.py spaces command
python3 -m dispatcher --space spaces/ANOTHER_SPACE_ID
```

### Monitor Logs

```bash
# Watch all events (including non-mentions) and bot responses in unified log
tail -f ../logs/google-chatbot.log
```

## Troubleshooting

### No events appearing in logs/google-chatbot.log

1. Verify Pub/Sub subscription is configured as Push:
   ```
   Go to Cloud Console > Pub/Sub > Subscriptions
   Check that endpoint URL is: https://example.com/cgi-bin/dispatcher.cgi
   ```

2. Check CGI script permissions:
   ```bash
   ls -l cgi-bin/dispatcher.cgi
   # Should be executable: -rwxr-xr-x
   ```

3. Check web server error logs for CGI errors

4. Test CGI endpoint manually:
   ```bash
   curl -X POST https://example.com/cgi-bin/dispatcher.cgi \
     -H "Content-Type: application/json" \
     -d '{"message": {"data": "eyJ0ZXN0IjogInRlc3QifQ=="}}'
   ```

### Events appearing but format is wrong

Check the raw event data in logs. Pub/Sub push format is:
```json
{
  "message": {
    "data": "base64-encoded-json",
    "messageId": "...",
    "publishTime": "..."
  },
  "subscription": "projects/.../subscriptions/..."
}
```

The data field contains base64-encoded Chat event JSON.

### Bot not responding to @mentions

The @mention bots (`chatbot.cgi`) and Events API (`dispatcher.cgi`) are separate systems. The Events API just logs; the bots still need to be @mentioned to respond.

### Config file issues

The dispatcher reads from `config/dispatcher.json`. If missing, it uses defaults:
- `project_id`: "project-y-433100"
- `topic_name`: "chat-message-events"

You can override these with command-line flags:
```bash
python3 -m dispatcher --space spaces/ABC123 --project my-project --topic my-topic
```

## Next Steps (Future Phases)

Current setup provides:
- ✅ Receive all messages in real-time
- ✅ Distinguish @mentions from regular messages
- ✅ Unified logging for all components
- ✅ Multi-bot support (Tachy and Raven)

Future phases will add:
- Filter messages for task-related keywords
- Extract task information
- Integration with task tracking
- External system webhooks
- Proactive task reminders
- Advanced filtering logic


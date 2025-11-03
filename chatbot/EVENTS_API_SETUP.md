# Google Workspace Events API Setup - Monitor All Messages

This document describes how to set up the Google Workspace Events API integration to receive **all Google Chat messages** (not just @mentions) via Push subscription.

**Use Case**: This setup is for scenarios where you need to monitor all messages in a space proactively, such as:
- Task tracking and compliance monitoring
- Automated logging and analytics
- Context-aware bot responses based on conversation flow

**Note**: If you only need the bot to respond when @mentioned, you don't need this setup - the standard bot configuration (google_chat_app.cgi) already handles that.

## Architecture

**Push Subscription Model:**
```
Google Chat → Events API → Pub/Sub Topic → Push to → chat_events.cgi → Logs
```

No daemon needed - Pub/Sub pushes events to your existing CGI infrastructure (just like the @mention bot).

## Prerequisites & Infrastructure Setup

### Step 1: Google Cloud Console Configuration

1. Navigate to [Google Cloud Console](https://console.cloud.google.com) for project `project-y-433100`

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
   - Click on existing `chat-message-events-sub` subscription
   - Click "Edit" at the top
   - Change "Delivery type" from "Pull" to "Push"
   - Set "Endpoint URL" to: `https://weiwu.au/cgi-bin/chat_events.cgi`
   - Leave "Enable authentication" unchecked for now (can add later)
   - Click "Update"

**Option B: Create new Push subscription**
   - Click "Create Subscription"
   - Subscription ID: `chat-message-events-push`
   - Select topic: `chat-message-events`
   - Delivery type: **Push**
   - Endpoint URL: `https://weiwu.au/cgi-bin/chat_events.cgi`
   - Leave authentication disabled for now
   - Click "Create"

### Step 3: Verify OAuth Credentials

Note: For Push subscriptions, you don't need a service account for receiving events - Pub/Sub pushes TO your CGI endpoint.

You only need OAuth credentials (already in `config/token.json`) for creating Workspace Events API subscriptions, which you already have from the existing bot setup.

## Deployment Instructions

### Step 1: Verify CGI Script

The CGI script is already created and executable:
```bash
ls -l cgi-bin/chat_events.cgi
# Should show: -rwxr-xr-x (executable)
```

### Step 2: Create Workspace Events Subscription

For each Google Chat space you want to monitor:

```bash
# List available spaces first
python3 google_chat_reporter.py spaces

# Create subscription for a space
python3 create_subscription.py --space spaces/YOUR_SPACE_ID
```

This creates a Workspace Events API subscription that tells Google Chat to send all message events from that space to your Pub/Sub topic, which then pushes them to your CGI endpoint.

### Step 3: Test the Integration

1. Post a message in the subscribed space (without @mentioning the bot)
2. Check the events log:
   ```bash
   tail -f logs/events.log
   ```
3. You should see:
   ```
   2025-11-02 15:30:00 - INFO - Space: Team Space | From: John Doe | Bot mentioned: False | Message: Testing the event system
   ```

4. Now @mention the bot in a message:
   ```
   @taskbot hello
   ```
5. Check both logs:
   ```bash
   tail -f logs/events.log    # Events API sees it
   tail -f logs/chatbot.log   # Bot endpoint also sees it
   ```

### Step 4: Verify Everything Works

Expected behaviour:

- **Regular messages** (no @mention):
  - Logged in `logs/events.log` with "Bot mentioned: False"
  - NOT sent to `logs/chatbot.log` (bot endpoint not called)
  
- **@mention messages**:
  - Logged in `logs/events.log` with "Bot mentioned: True"
  - ALSO logged in `logs/chatbot.log` (bot endpoint called)
  - Bot responds with "Hey, hello"

## File Structure

```
/home/weiwu/code/Google-Spaces-Tasks-reporter.web/
├── cgi-bin/
│   ├── google_chat_app.cgi       (EXISTING - @mention bot)
│   └── chat_events.cgi           (NEW - receives all messages)
├── create_subscription.py        (NEW - creates Events API subscriptions)
├── logs/
│   ├── chatbot.log               (EXISTING - @mention bot logs)
│   └── events.log                (NEW - all message events)
└── config/
    └── token.json                (EXISTING - OAuth credentials)
```

## Usage

### List Active Subscriptions

```bash
python3 create_subscription.py --list
```

### Create Subscription for Additional Spaces

```bash
# Get space ID from google_chat_reporter.py spaces command
python3 create_subscription.py --space spaces/ANOTHER_SPACE_ID
```

### Monitor Logs

```bash
# Watch all events (including non-mentions)
tail -f logs/events.log

# Watch bot responses (only @mentions)
tail -f logs/chatbot.log
```

## Troubleshooting

### No events appearing in logs/events.log

1. Verify Pub/Sub subscription is configured as Push:
   ```
   Go to Cloud Console > Pub/Sub > Subscriptions
   Check that endpoint URL is: https://weiwu.au/cgi-bin/chat_events.cgi
   ```

2. Check CGI script permissions:
   ```bash
   ls -l cgi-bin/chat_events.cgi
   # Should be executable: -rwxr-xr-x
   ```

3. Check web server error logs for CGI errors

4. Test CGI endpoint manually:
   ```bash
   curl -X POST https://weiwu.au/cgi-bin/chat_events.cgi \
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

The @mention bot (`google_chat_app.cgi`) and Events API (`chat_events.cgi`) are separate systems. The Events API just logs; the bot still needs to be @mentioned to respond.

## Next Steps (Future Phases)

Phase 2 complete provides:
- ✅ Receive all messages in real-time
- ✅ Distinguish @mentions from regular messages
- ✅ Log all activity

Phase 3 will add:
- Filter messages for task-related keywords
- Extract task information
- Integration with task tracking

Phase 4 will add:
- External system webhooks
- Proactive task reminders
- Advanced filtering logic


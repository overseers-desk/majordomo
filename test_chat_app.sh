#!/bin/bash
# Test script to emulate Google Chat event calls
# This script sends sample event payloads to test the CGI dispatcher

echo "Testing Google Chat App"
echo "======================="
echo ""

# Define the Chat App URL
# Change this to match your server configuration
CHAT_APP_URL="http://localhost/google_chat_app.cgi"

echo "Using Chat App URL: $CHAT_APP_URL"
echo ""

# Test 1: Simple MESSAGE event with long message
echo "Test 1: MESSAGE event with long message"
echo "----------------------------------------"
curl -X POST "$CHAT_APP_URL" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "MESSAGE",
    "message": {
      "sender": {
        "displayName": "Test User",
        "email": "testuser@example.com"
      },
      "text": "Hello, this is a test message from Google Chat to verify the app is working correctly and sending responses",
      "createTime": "2025-11-02T12:00:00.000Z",
      "space": {
        "name": "spaces/AAAA1234",
        "displayName": "Test Space"
      },
      "thread": {
        "name": "spaces/AAAA1234/threads/THREAD123"
      }
    },
    "space": {
      "name": "spaces/AAAA1234",
      "displayName": "Test Space"
    }
  }'
echo ""
echo ""

# Test 2: Short message
echo "Test 2: MESSAGE event with short message"
echo "-----------------------------------------"
curl -X POST "$CHAT_APP_URL" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "MESSAGE",
    "message": {
      "sender": {
        "displayName": "Jane Doe",
        "email": "jane@example.com"
      },
      "text": "Quick test",
      "createTime": "2025-11-02T12:01:00.000Z"
    }
  }'
echo ""
echo ""

# Test 3: ADDED_TO_SPACE event (no message text)
echo "Test 3: ADDED_TO_SPACE event"
echo "-----------------------------"
curl -X POST "$CHAT_APP_URL" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "ADDED_TO_SPACE",
    "space": {
      "name": "spaces/AAAA5678",
      "displayName": "New Test Space"
    },
    "user": {
      "displayName": "Bot Admin",
      "email": "admin@example.com"
    }
  }'
echo ""
echo ""

# Test 4: MESSAGE with @ mention
echo "Test 4: MESSAGE with @mention"
echo "------------------------------"
curl -X POST "$CHAT_APP_URL" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "MESSAGE",
    "message": {
      "sender": {
        "displayName": "Alice Smith",
        "email": "alice@example.com"
      },
      "text": "@bot please help me with this task I need assistance",
      "createTime": "2025-11-02T12:02:00.000Z"
    }
  }'
echo ""
echo ""

# Test 5: Empty/invalid request (should log error)
echo "Test 5: Invalid JSON payload (error test)"
echo "------------------------------------------"
curl -X POST "$CHAT_APP_URL" \
  -H "Content-Type: application/json" \
  -d '{invalid json}'
echo ""
echo ""

echo "========================================="
echo "Tests completed!"
echo ""
echo "Check the log file to verify results:"
echo "  tail -f logs/chatbot.log"
echo ""
echo "Expected log entries:"
echo "  - Event: MESSAGE | From: Test User | Message: Hello, this is a..."
echo "  - Response sent successfully to spaces/AAAA1234"
echo "  - Event: MESSAGE | From: Jane Doe | Message: Quick test"
echo "  - Response sent successfully to spaces/AAAA1234"
echo "  - Event: ADDED_TO_SPACE | From: Unknown | Message: (no message text)"
echo "  - Event: MESSAGE | From: Alice Smith | Message: @bot please help me..."
echo "  - Response sent successfully to spaces/AAAA1234"
echo "  - Error log for invalid JSON"
echo ""
echo "Note: Responses won't actually post to Google Chat during local testing."
echo "Deploy to server and test in a real space to see actual responses."


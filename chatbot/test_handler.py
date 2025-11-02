#!/usr/bin/env python3
"""
Direct test of the chatbot handler without CGI.
This tests the handler module directly to ensure it processes events correctly.
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chatbot.handler import setup_logging, process_webhook_event

# Setup logging
setup_logging()

print("Testing chatbot handler directly...")
print("=" * 60)

# Test 1: MESSAGE event with long message
print("\nTest 1: MESSAGE event with long message")
event1 = {
    "type": "MESSAGE",
    "message": {
        "sender": {
            "displayName": "Test User",
            "email": "testuser@example.com"
        },
        "text": "Hello, this is a test message from Google Spaces to verify the webhook is working correctly and logging information properly",
        "createTime": "2025-11-02T12:00:00.000Z"
    }
}

result1 = process_webhook_event(event1)
print(f"Result: {result1}")

# Test 2: Short message
print("\nTest 2: MESSAGE event with short message")
event2 = {
    "type": "MESSAGE",
    "message": {
        "sender": {
            "displayName": "Jane Doe"
        },
        "text": "Quick test",
        "createTime": "2025-11-02T12:01:00.000Z"
    }
}

result2 = process_webhook_event(event2)
print(f"Result: {result2}")

# Test 3: ADDED_TO_SPACE (no message text)
print("\nTest 3: ADDED_TO_SPACE event (no message)")
event3 = {
    "type": "ADDED_TO_SPACE",
    "space": {
        "name": "spaces/AAAA5678",
        "displayName": "New Test Space"
    },
    "user": {
        "displayName": "Bot Admin"
    }
}

result3 = process_webhook_event(event3)
print(f"Result: {result3}")

# Test 4: MESSAGE with @mention
print("\nTest 4: MESSAGE with @mention")
event4 = {
    "type": "MESSAGE",
    "message": {
        "sender": {
            "displayName": "Alice Smith"
        },
        "text": "@bot please help me with this task I need assistance urgently",
        "createTime": "2025-11-02T12:02:00.000Z"
    }
}

result4 = process_webhook_event(event4)
print(f"Result: {result4}")

print("\n" + "=" * 60)
print("Tests completed!")
print("\nCheck logs/chatbot.log for log entries:")
print("  tail -f logs/chatbot.log")
print("\nExpected entries:")
print("  - Event: MESSAGE | From: Test User | Message: Hello, this is a...")
print("  - Event: MESSAGE | From: Jane Doe | Message: Quick test")
print("  - Event: ADDED_TO_SPACE | From: Unknown | Message: (no message text)")
print("  - Event: MESSAGE | From: Alice Smith | Message: @bot please help me...")


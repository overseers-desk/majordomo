#!/usr/bin/env python3
"""
Create Workspace Events API Subscription

One-time script to create a subscription for a Google Chat space to receive
all message events via Pub/Sub.
"""

import argparse
import sys
from google_chat_reporter import get_credentials, setup_logging
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


def create_message_subscription(space_name, pubsub_topic):
    """
    Create a subscription to receive all message events in a space.
    
    Args:
        space_name (str): Space name (e.g., 'spaces/ABC123')
        pubsub_topic (str): Full Pub/Sub topic name
    
    Returns:
        dict: Created subscription details
    """
    creds = get_credentials()
    service = build('workspaceevents', 'v1', credentials=creds)
    
    subscription = {
        'targetResource': f'//chat.googleapis.com/{space_name}',
        'eventTypes': ['google.workspace.chat.message.v1.created'],
        'notificationEndpoint': {
            'pubsubTopic': pubsub_topic
        },
        'payloadOptions': {
            'includeResource': True
        }
    }
    
    try:
        result = service.subscriptions().create(body=subscription).execute()
        print(f"✓ Created subscription: {result.get('name')}")
        print(f"  Watching space: {space_name}")
        print(f"  Events will be pushed to: {pubsub_topic}")
        return result
    except HttpError as e:
        print(f"✗ Failed to create subscription: {e}")
        sys.exit(1)


def list_subscriptions():
    """List all active Workspace Events subscriptions."""
    creds = get_credentials()
    service = build('workspaceevents', 'v1', credentials=creds)
    
    try:
        result = service.subscriptions().list().execute()
        subscriptions = result.get('subscriptions', [])
        
        if subscriptions:
            print(f"Found {len(subscriptions)} active subscription(s):\n")
            for sub in subscriptions:
                print(f"Name: {sub.get('name')}")
                print(f"  Resource: {sub.get('targetResource')}")
                print(f"  Events: {', '.join(sub.get('eventTypes', []))}")
                print()
        else:
            print("No active subscriptions found")
        
        return subscriptions
    except HttpError as e:
        print(f"✗ Failed to list subscriptions: {e}")
        sys.exit(1)


def main():
    setup_logging()
    
    parser = argparse.ArgumentParser(
        description='Manage Workspace Events API subscriptions for Google Chat'
    )
    parser.add_argument(
        '--space',
        help='Space name to monitor (e.g., spaces/ABC123)'
    )
    parser.add_argument(
        '--project',
        default='project-y-433100',
        help='GCP project ID (default: project-y-433100)'
    )
    parser.add_argument(
        '--topic',
        default='chat-message-events',
        help='Pub/Sub topic name (default: chat-message-events)'
    )
    parser.add_argument(
        '--list',
        action='store_true',
        help='List all active subscriptions'
    )
    
    args = parser.parse_args()
    
    if args.list:
        list_subscriptions()
    elif args.space:
        pubsub_topic = f'projects/{args.project}/topics/{args.topic}'
        create_message_subscription(args.space, pubsub_topic)
    else:
        parser.print_help()
        print("\nExamples:")
        print("  List subscriptions:")
        print("    python3 create_subscription.py --list")
        print("\n  Create subscription for a space:")
        print("    python3 create_subscription.py --space spaces/ABC123")


if __name__ == '__main__':
    main()


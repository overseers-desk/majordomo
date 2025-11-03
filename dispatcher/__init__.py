"""
Dispatcher utilities for managing Workspace Events API subscriptions.

Provides CLI interface for creating and listing Google Chat event subscriptions.
Can be called from command line: python3 -m dispatcher [options]
"""

import argparse
import sys
import os
import json
from google_chat_reporter import get_credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


def _load_config():
    """
    Load dispatcher configuration from config/dispatcher.json.
    
    Returns:
        dict: Configuration with pubsub_topic (full path) and optionally pubsub_subscription
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_file = os.path.join(project_root, 'config', 'dispatcher.json')
    
    # Default config - use full paths
    default_config = {
        'pubsub_topic': 'projects/project-y-433100/topics/chat-message-events',
        'pubsub_subscription': 'projects/project-y-433100/subscriptions/chat-message-events-sub'
    }
    
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
                # Merge with defaults
                default_config.update(config)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Could not read dispatcher config from {config_file}: {e}")
            print(f"Using defaults: {default_config}")
    
    return default_config


def create_message_subscription(space_name, pubsub_topic=None):
    """
    Create a subscription to receive all message events in a space.
    
    Args:
        space_name (str): Space name (e.g., 'spaces/ABC123')
        pubsub_topic (str, optional): Full Pub/Sub topic name. 
                                     If None, uses config defaults.
    
    Returns:
        dict: Created subscription details
    """
    # Load config if topic not provided
    if not pubsub_topic:
        config = _load_config()
        pubsub_topic = config['pubsub_topic']
    
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
    """CLI entry point for dispatcher utilities."""
    config = _load_config()
    
    parser = argparse.ArgumentParser(
        description='Manage Workspace Events API subscriptions for Google Chat'
    )
    parser.add_argument(
        '--space',
        help='Space name to monitor (e.g., spaces/ABC123)'
    )
    parser.add_argument(
        '--topic',
        default=config.get('pubsub_topic'),
        help=f"Full Pub/Sub topic path (default: {config.get('pubsub_topic')})"
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
        pubsub_topic = args.topic or config['pubsub_topic']
        create_message_subscription(args.space, pubsub_topic)
    else:
        parser.print_help()
        print("\nExamples:")
        print("  List subscriptions:")
        print("    python3 -m dispatcher --list")
        print("\n  Create subscription for a space:")
        print("    python3 -m dispatcher --space spaces/ABC123")


if __name__ == '__main__':
    main()


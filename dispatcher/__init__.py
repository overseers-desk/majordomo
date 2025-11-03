"""
Dispatcher utilities for managing Workspace Events API subscriptions.

Provides CLI interface for creating and listing Google Chat event subscriptions.
Can be called from command line: python3 -m dispatcher [options]
"""

import argparse
import sys
import os
import json
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.errors import HttpError

# Scopes needed for Workspace Events API
SCOPES = [
    'https://www.googleapis.com/auth/chat.spaces',
    'https://www.googleapis.com/auth/chat.messages',
    'https://www.googleapis.com/auth/chat.messages.readonly',
]


def _get_credentials_paths():
    """
    Get credential file paths, checking dispatcher-specific location first, then fallback.
    
    Returns:
        tuple: (token_file, credentials_file) paths
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    # Priority 1: config/dispatcher/ (dispatcher-specific credentials)
    dispatcher_token = os.path.join(project_root, 'config', 'dispatcher', 'token.json')
    dispatcher_secret = os.path.join(project_root, 'config', 'dispatcher', 'client_secret.json')
    
    # Priority 2: config/ (top-level fallback)
    top_level_token = os.path.join(project_root, 'config', 'token.json')
    top_level_secret = os.path.join(project_root, 'config', 'client_secret.json')
    
    # Priority: dispatcher-specific > top-level > dispatcher (for new files)
    if os.path.exists(dispatcher_token) and os.path.exists(dispatcher_secret):
        # Both dispatcher files exist, use them
        return dispatcher_token, dispatcher_secret
    elif os.path.exists(dispatcher_secret):
        # Dispatcher secret exists but not token - use dispatcher location (will create token there)
        return dispatcher_token, dispatcher_secret
    elif os.path.exists(top_level_token) and os.path.exists(top_level_secret):
        # Both top-level files exist, use them as fallback
        return top_level_token, top_level_secret
    elif os.path.exists(top_level_secret):
        # Top-level secret exists but not token - use top-level location
        return top_level_token, top_level_secret
    else:
        # No credentials found - default to dispatcher location (will prompt user to create)
        return dispatcher_token, dispatcher_secret


def get_credentials() -> Credentials:
    """
    Fetch or refresh Google API credentials for dispatcher.
    
    Checks config/dispatcher/ first, then falls back to config/.
    """
    token_file, credentials_file = _get_credentials_paths()
    
    creds = None
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(credentials_file):
                raise FileNotFoundError(
                    f"Credentials file not found. Expected at: {credentials_file}\n"
                    f"Or fallback location: {os.path.join(os.path.dirname(os.path.dirname(credentials_file)), 'client_secret.json')}"
                )
            flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
            creds = flow.run_local_server(port=7276, access_type="offline", prompt='consent')
        with open(token_file, 'w') as token:
            token.write(creds.to_json())

    return creds


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


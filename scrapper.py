import os
import logging
import json
import argparse
from datetime import datetime, timedelta
from typing import List, Dict
import unicodedata
import pandas as pd
import time

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.errors import HttpError

# Constants
SCOPES = [
    'https://www.googleapis.com/auth/chat.spaces',
    'https://www.googleapis.com/auth/chat.messages',
    'https://www.googleapis.com/auth/chat.messages.readonly',
    'https://www.googleapis.com/auth/tasks.readonly',
]
TOKEN_FILE = 'token.json'
CREDENTIALS_FILE = 'client_secret.json'

def setup_logging():
    """Setup logging configuration."""
    # Suppress specific warnings from Google API client
    logging.getLogger('googleapiclient.discovery_cache').setLevel(logging.ERROR)
    logging.getLogger('googleapiclient.discovery').setLevel(logging.ERROR)
    
    logging.basicConfig(
        level=logging.INFO,  # Changed back to INFO from DEBUG
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

def get_credentials() -> Credentials:
    """Fetch or refresh Google API credentials."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=7276, access_type="offline", prompt='consent')
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())

    return creds

def retry_on_error(max_retries=3, delay=30):
    """
    Decorator that retries a function on failure with a delay.
    
    Args:
        max_retries (int): Maximum number of retry attempts
        delay (int): Delay in seconds between retries
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            retries = 0
            while retries < max_retries:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    retries += 1
                    if retries == max_retries:
                        logging.error(f"Failed after {max_retries} attempts: {str(e)}")
                        raise
                    logging.warning(f"Attempt {retries} failed: {str(e)}. Retrying in {delay} seconds...")
                    time.sleep(delay)
            return None
        return wrapper
    return decorator

@retry_on_error()
def get_spaces(service) -> List[Dict]:
    """Retrieve all spaces from Google Chat, excluding DIRECT_MESSAGE spaces."""
    spaces = []
    page_token = None
    while True:
        response = service.spaces().list(pageToken=page_token).execute()
        for space in response.get('spaces', []):
            if space.get('spaceType') != 'SPACE':  # Exclude DIRECT_MESSAGE spaces
                continue
            spaces.append(space)
        page_token = response.get('nextPageToken')
        if not page_token:
            break
    return spaces

def get_public_spaces(service) -> List[Dict]:
    """Retrieve only public spaces (SPACE type)."""
    return get_spaces(service)  # Reuse existing function

def get_direct_message_spaces(service) -> List[Dict]:
    """Retrieve only direct message spaces (DIRECT_MESSAGE type)."""
    spaces = []
    page_token = None
    while True:
        response = service.spaces().list(pageToken=page_token).execute()
        for space in response.get('spaces', []):
            if space.get('spaceType') == 'DIRECT_MESSAGE':  # Only include DIRECT_MESSAGE spaces
                spaces.append(space)
        page_token = response.get('nextPageToken')
        if not page_token:
            break
    return spaces

def get_all_spaces_and_dms(service) -> List[Dict]:
    """Retrieve all spaces including direct messages."""
    spaces = []
    page_token = None
    while True:
        response = service.spaces().list(pageToken=page_token).execute()
        spaces.extend(response.get('spaces', []))
        page_token = response.get('nextPageToken')
        if not page_token:
            break
    return spaces

def normalize_name(name: str) -> str:
    """Normalize a name by removing accents and special characters."""
    # Normalize the name to NFKD form (decompose accents)
    normalized = unicodedata.normalize('NFKD', name)
    # Remove non-ASCII characters (e.g., accents)
    normalized = normalized.encode('ascii', 'ignore').decode('ascii')
    # Convert to lowercase and strip whitespace
    normalized = normalized.lower().strip()
    return normalized

def save_to_json(data: List[Dict], filename: str):
    """Save data to a JSON file with UTF-8 encoding."""
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)  # Ensure non-ASCII characters are preserved
    logging.info(f"Data saved to {filename}")

def save_to_csv(data: List[Dict], filename: str):
    """Save data to a CSV file with UTF-8 encoding and headers."""
    if not data:
        logging.warning("No data to save to CSV")
        return
    
    df = pd.DataFrame(data)
    df.to_csv(filename, index=False, encoding='utf-8')
    logging.info(f"Data saved to {filename}")

def save_data(data: List[Dict], filename: str, output_format: str = "json"):
    """Save data in the specified format (json or csv)."""
    if output_format.lower() == "csv":
        save_to_csv(data, filename)
    else:  # Default to JSON
        save_to_json(data, filename)

def load_from_json(filename: str) -> List[Dict]:
    """Load data from a JSON file."""
    if os.path.exists(filename):
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

@retry_on_error()
def get_user_display_name(creds: Credentials, user_resource_name: str) -> str:
    """Fetch the display name of a user using the Google People API."""
    try:
        if user_resource_name.startswith('users/'):
            user_resource_name = user_resource_name.replace('users/', 'people/')

        people_service = build('people', 'v1', credentials=creds)
        profile = people_service.people().get(
            resourceName=user_resource_name,
            personFields='names'
        ).execute()

        if 'names' in profile:
            for name in profile['names']:
                if 'displayName' in name:
                    return name['displayName']
    except Exception as e:
        logging.error(f"Error fetching profile for user {user_resource_name}: {e}")
        raise
    return None


@retry_on_error()
def get_messages_for_space(service, space_name: str, date_start: str, date_end: str):
    """Helper function to get messages from a space with retry logic."""
    page_token = None
    messages = []
    while True:
        response = service.spaces().messages().list(
            parent=space_name,
            pageToken=page_token,
            filter=f'createTime > "{date_start}" AND createTime < "{date_end}"'
        ).execute()
        messages.extend(response.get('messages', []))
        page_token = response.get('nextPageToken')
        if not page_token:
            break
    return messages

def get_people(service, spaces: List[Dict], start_date: str = None, end_date: str = None) -> List[str]:
    """Retrieve a list of unique people from SPACE type spaces by scraping messages."""
    people = set()
    for space in spaces:
        if space.get('spaceType') != 'SPACE':
            continue

        logging.info(f"Processing space: {space['name']}")
        
        try:
            messages = get_messages_for_space(service, space['name'], start_date, end_date)
            for message in messages:
                if 'sender' in message and 'displayName' in message['sender']:
                    people.add(message['sender']['displayName'])

                if 'text' in message and 'via Tasks' in message['text']:
                    text = message['text']
                    if "@" in text:
                        assignee = text.split("@")[1].split("(")[0].strip()
                        assignee = assignee.split(" to")[0].strip()
                        people.add(assignee)
        except Exception as e:
            logging.error(f"Error processing space {space['name']}: {e}")
            continue

    return list(people)

def get_tasks(service, space_name: str, start_date: str, end_date: str) -> List[Dict]:
    """Retrieve tasks from a specific space within a date range using a valid filter query."""
    tasks = []
    completed_tasks, reopened_tasks, deleted_tasks, assigned_tasks = set(), set(), set(), set()
    
    try:
        messages = get_messages_for_space(service, space_name, start_date, end_date)
        
        for message in messages:
            if 'via Tasks' in message.get('text', ''):
                task_id = message['thread']['name'].split("/")[3]
                text = message['text']
                assignee = text.split("@")[1].split("(")[0].strip() if "@" in text else "Unassigned"

                if "Created" in text:
                    task_data = {
                        'id': task_id,
                        'assignee': assignee,
                        'status': 'OPEN',
                        'created_time': message['createTime'],
                        'space_name': space_name,
                        'message_text': message.get('text', ''),
                        'sender': message.get('sender', {}).get('displayName', 'Unknown'),
                        'thread_name': message.get('thread', {}).get('name', ''),
                    }
                    tasks.append(task_data)
                elif "Assigned" in text:
                    assigned_tasks.add(task_id + "@" + assignee)
                elif "Completed" in text:
                    completed_tasks.add(task_id)
                elif "Deleted" in text:
                    deleted_tasks.add(task_id)
                elif "Re-opened" in text:
                    reopened_tasks.add(task_id)

    except Exception as e:
        logging.error(f"Error fetching tasks from space {space_name}: {e}")
        raise

    # Update task statuses
    for task in tasks:
        task_id = task['id']
        if task_id in deleted_tasks:
            tasks.remove(task)

        for assigned in assigned_tasks:
            new_assignment = assigned.split("@")
            tid = new_assignment[0]
            t_assignee = new_assignment[1]

            if tid == task['id']:
                task['assignee'] = t_assignee
                continue

        if task_id in completed_tasks:
            task['status'] = 'COMPLETED'
        elif task_id in reopened_tasks:
            task['status'] = 'OPEN'

    return tasks

def analyze_tasks(tasks: List[Dict]) -> pd.DataFrame:
    """Analyze tasks and generate a report with tasks received, completed, and completion rate."""
    if not tasks:
        logging.warning("No tasks found to analyze.")
        return pd.DataFrame(columns=['assignee', 'tasks_received', 'tasks_completed', 'completion_rate'])

    df = pd.DataFrame(tasks)

    # Group by assignee and calculate tasks received and completed
    total_tasks = df.groupby('assignee').size().rename('tasks_received')
    completed_tasks = df[df['status'] == 'COMPLETED'].groupby('assignee').size().rename('tasks_completed')

    # Merge the results into a single DataFrame
    report = pd.concat([total_tasks, completed_tasks], axis=1).fillna(0)

    # Calculate completion rate
    report['completion_rate'] = report['tasks_completed'] / report['tasks_received']

    # Reset index to make 'assignee' a column
    report.reset_index(inplace=True)
    report.rename(columns={'index': 'assignee'}, inplace=True)

    return report

def filter_tasks(tasks: List[Dict], people: List[str], spaces: List[str]) -> List[Dict]:
    """Filter tasks to only include people and spaces listed in people.json and spaces.json."""
    # Normalize the list of people
    normalized_people = {normalize_name(person) for person in people}
    normalized_spaces = {space for space in spaces}

    filtered_tasks = []
    for task in tasks:
        # Normalize the assignee name
        normalized_assignee = normalize_name(task['assignee'])
        if normalized_assignee in normalized_people and task['space_name'] in normalized_spaces:
            filtered_tasks.append(task)
    return filtered_tasks

def generate_report(report: pd.DataFrame, start_date: str, end_date: str):
    """Generate and save the task report as a CSV file."""
    # Convert dates to ISO format for filename (assuming they're in RFC3339 format)
    start_iso = datetime.fromisoformat(start_date.replace('Z', '')).strftime('%Y-%m-%d')
    end_iso = datetime.fromisoformat(end_date.replace('Z', '')).strftime('%Y-%m-%d')
    
    file_name = f'task_report_{start_iso}_{end_iso}.csv'
    report.to_csv(file_name, index=False)
    
    # Print date range and report
    logging.info(f"\nTask Report for period: {start_iso} to {end_iso}")
    logging.info(report)
    logging.info(f"\nReport saved as {file_name}")

def get_default_dates():
    """Get the default date range for the previous calendar month in RFC 3339 format."""
    today = datetime.today()
    first_day_of_month = today.replace(day=1)
    last_day_of_previous_month = first_day_of_month - timedelta(days=1)
    first_day_of_previous_month = last_day_of_previous_month.replace(day=1)
    return (
        first_day_of_previous_month.isoformat() + "Z",  # Start date
        last_day_of_previous_month.isoformat() + "Z"    # End date
    )

def get_past_day_dates():
    """Get the date range for the past day (1 day ago to today) in RFC 3339 format."""
    today = datetime.today()
    past_day = today - timedelta(days=1)
    return (
        past_day.isoformat() + "Z",  # Start date
        today.isoformat() + "Z"      # End date
    )

def get_past_month_dates():
    """Get the date range for the past month (30 days ago to today) in RFC 3339 format."""
    today = datetime.today()
    past_month = today - timedelta(days=30)
    return (
        past_month.isoformat() + "Z",  # Start date
        today.isoformat() + "Z"        # End date
    )

def get_past_year_dates():
    """Get the date range for the past year (365 days ago to today) in RFC 3339 format."""
    today = datetime.today()
    past_year = today - timedelta(days=365)
    return (
        past_year.isoformat() + "Z",  # Start date
        today.isoformat() + "Z"       # End date
    )

def convert_to_rfc3339(date_str: str) -> str:
    """Convert an ISO format date (e.g., 2022-01-15) to RFC 3339 format (e.g., 2022-01-15T00:00:00Z)."""
    try:
        # Parse the input date string
        date_obj = datetime.fromisoformat(date_str)
        # Convert to RFC 3339 format
        return date_obj.isoformat() + "Z"
    except ValueError:
        raise ValueError(f"Invalid date format: {date_str}. Expected format: YYYY-MM-DD")

def parse_date_range(args) -> tuple[str, str]:
    """
    Parse date range arguments and return start and end dates in RFC 3339 format.
    
    Args:
        args: Command line arguments object containing date-related flags
        
    Returns:
        tuple: (date_start, date_end) in RFC 3339 format
        
    Raises:
        ValueError: If date parsing fails or invalid date combination is provided
    """
    try:
        # Handle date range options with priority: past-day > past-month/past-year > custom dates > default dates
        if hasattr(args, 'past_day') and args.past_day:
            date_start, date_end = get_past_day_dates()
            logging.info("Using past day date range (1 day ago to today)")
        elif hasattr(args, 'past_month') and args.past_month:
            date_start, date_end = get_past_month_dates()
            logging.info("Using past month date range (30 days ago to today)")
        elif hasattr(args, 'past_year') and args.past_year:
            date_start, date_end = get_past_year_dates()
            logging.info("Using past year date range (365 days ago to today)")
        elif hasattr(args, 'date_start') and hasattr(args, 'date_end') and args.date_start and args.date_end:
            date_start = convert_to_rfc3339(args.date_start)
            date_end = convert_to_rfc3339(args.date_end)
        elif hasattr(args, 'date_start') and hasattr(args, 'date_end') and (args.date_start or args.date_end):
            logging.error("Both --date-start and --date-end must be provided together")
            raise ValueError("Both --date-start and --date-end must be provided together")
        else:
            date_start, date_end = get_default_dates()
            logging.info("Using default date range (previous calendar month)")
        
        return date_start, date_end
    except Exception as e:
        if isinstance(e, ValueError):
            raise
        logging.error(f"Error parsing date range: {e}")
        raise ValueError(f"Error parsing date range: {e}")

def format_task_info(task: Dict, space_name: str) -> Dict:
    """Format task information in a human-friendly way."""
    return {
        'id': task['id'],
        'assignee': task.get('assignee', 'Unassigned'),
        'status': task.get('status', 'UNKNOWN'),
        'space': space_name,
        'created_at': task.get('created_time'),
        'last_updated': task.get('last_update_time', task.get('created_time')),
        'message_text': task.get('message_text', ''),
        'sender': task.get('sender', ''),
        'thread_name': task.get('thread_name', ''),
    }

def get_formatted_tasks(service, spaces: List[Dict], start_date: str = None, end_date: str = None) -> List[Dict]:
    """Retrieve formatted task information from specified spaces."""
    formatted_tasks = []
    
    for space in spaces:
        space_name = space.get('displayName', space['name'])
        logging.info(f"Fetching tasks from space: {space_name}")
        
        try:
            tasks = get_tasks(service, space['name'], start_date, end_date)
            for task in tasks:
                formatted_task = format_task_info(task, space_name)
                formatted_tasks.append(formatted_task)
        except Exception as e:
            logging.error(f"Error fetching tasks from space {space_name}: {e}")
            continue
            
    return formatted_tasks

@retry_on_error()
def get_task_details_from_tasks_api(creds: Credentials, task_id: str) -> Dict:
    """Try to get task details from Google Tasks API using the task ID."""
    try:
        tasks_service = build('tasks', 'v1', credentials=creds)
        
        # First, try to get all task lists
        tasklists = tasks_service.tasklists().list().execute()
        
        for tasklist in tasklists.get('items', []):
            try:
                # Try to get the specific task from this tasklist
                task = tasks_service.tasks().get(
                    tasklist=tasklist['id'],
                    task=task_id
                ).execute()
                
                return {
                    'title': task.get('title', 'Unknown Task'),
                    'notes': task.get('notes', ''),
                    'status': task.get('status', 'needsAction'),
                    'due': task.get('due', ''),
                    'completed': task.get('completed', ''),
                    'updated': task.get('updated', ''),
                    'tasklist_id': tasklist['id'],
                    'tasklist_title': tasklist.get('title', 'Unknown List')
                }
            except HttpError as e:
                if e.resp.status == 404:
                    # Task not found in this list, try next
                    continue
                else:
                    raise
        
        # If not found in any tasklist, return None
        return None
        
    except Exception as e:
        logging.warning(f"Could not fetch task details for {task_id}: {e}")
        return None

@retry_on_error()
def get_all_tasks_from_tasks_api(creds: Credentials, start_date: str, end_date: str) -> List[Dict]:
    """Fetch all tasks from Google Tasks API within a time range."""
    try:
        tasks_service = build('tasks', 'v1', credentials=creds)
        all_tasks = []
        
        # Get all task lists
        tasklists = tasks_service.tasklists().list().execute()
        
        for tasklist in tasklists.get('items', []):
            try:
                # Get all tasks from this tasklist
                tasks = tasks_service.tasks().list(
                    tasklist=tasklist['id'],
                    showCompleted=True,
                    showDeleted=True,
                    showHidden=True
                ).execute()
                
                for task in tasks.get('items', []):
                    # Parse task creation time
                    task_created = task.get('created', '')
                    if task_created:
                        # Convert to datetime for comparison
                        try:
                            task_created_dt = datetime.fromisoformat(task_created.replace('Z', ''))
                            start_dt = datetime.fromisoformat(start_date.replace('Z', ''))
                            end_dt = datetime.fromisoformat(end_date.replace('Z', ''))
                            
                            # Check if task was created within the time range
                            if start_dt <= task_created_dt <= end_dt:
                                all_tasks.append({
                                    'id': task.get('id', ''),
                                    'title': task.get('title', 'Unknown Task'),
                                    'notes': task.get('notes', ''),
                                    'status': task.get('status', 'needsAction'),
                                    'due': task.get('due', ''),
                                    'completed': task.get('completed', ''),
                                    'updated': task.get('updated', ''),
                                    'created': task_created,
                                    'tasklist_id': tasklist['id'],
                                    'tasklist_title': tasklist.get('title', 'Unknown List')
                                })
                        except ValueError as e:
                            logging.warning(f"Could not parse task creation time {task_created}: {e}")
                            continue
                            
            except Exception as e:
                logging.warning(f"Error fetching tasks from list {tasklist.get('title', 'Unknown')}: {e}")
                continue
        
        logging.info(f"Found {len(all_tasks)} tasks from Google Tasks API in the specified time range")
        return all_tasks
        
    except Exception as e:
        logging.error(f"Error fetching tasks from Google Tasks API: {e}")
        return []

def get_tasks_for_assignee(service, space_name: str, assignee_name: str, start_date: str, end_date: str, creds: Credentials = None) -> List[Dict]:
    """Retrieve tasks assigned to a specific person with detailed information."""
    tasks = []
    completed_tasks, reopened_tasks, deleted_tasks, assigned_tasks = set(), set(), set(), set()
    
    try:
        messages = get_messages_for_space(service, space_name, start_date, end_date)
        
        # First pass: collect all task-related messages
        task_messages = {}
        for message in messages:
            if 'via Tasks' in message.get('text', ''):
                task_id = message['thread']['name'].split("/")[3]
                text = message['text']
                create_time = message['createTime']
                sender = message.get('sender', {}).get('displayName', 'Unknown')
                
                if task_id not in task_messages:
                    task_messages[task_id] = {
                        'created': None,
                        'assigned': [],
                        'completed': [],
                        'reopened': [],
                        'deleted': [],
                        'updates': []
                    }
                
                # Categorize messages
                if "Created" in text:
                    assignee = text.split("@")[1].split("(")[0].strip() if "@" in text else "Unassigned"
                    task_messages[task_id]['created'] = {
                        'assignee': assignee,
                        'time': create_time,
                        'text': text,
                        'sender': sender
                    }
                elif "Assigned" in text:
                    assignee = text.split("@")[1].split("(")[0].strip() if "@" in text else "Unassigned"
                    task_messages[task_id]['assigned'].append({
                        'assignee': assignee,
                        'time': create_time,
                        'text': text,
                        'sender': sender
                    })
                elif "Completed" in text:
                    task_messages[task_id]['completed'].append({
                        'time': create_time,
                        'text': text,
                        'sender': sender
                    })
                elif "Deleted" in text:
                    task_messages[task_id]['deleted'].append({
                        'time': create_time,
                        'text': text,
                        'sender': sender
                    })
                elif "Re-opened" in text:
                    task_messages[task_id]['reopened'].append({
                        'time': create_time,
                        'text': text,
                        'sender': sender
                    })
                else:
                    # General updates
                    task_messages[task_id]['updates'].append({
                        'time': create_time,
                        'text': text,
                        'sender': sender
                    })
        
        # Second pass: process tasks and find those assigned to the target person
        for task_id, task_data in task_messages.items():
            if not task_data['created']:
                continue
                
            # Check if task is assigned to the target person
            current_assignee = task_data['created']['assignee']
            
            # Check all assignment messages to find current assignee
            for assignment in task_data['assigned']:
                current_assignee = assignment['assignee']
            
            # Normalize names for comparison
            # Handle cases where assignee name might be stored differently than in the message
            # e.g., message shows "@Priyanka D (P)" but assignee is stored as "Priyanka D"
            normalized_current = normalize_name(current_assignee)
            normalized_target = normalize_name(assignee_name)
            
            # Also try matching without the parenthetical part
            normalized_target_no_paren = normalize_name(assignee_name.split('(')[0].strip())
            
            if normalized_current != normalized_target and normalized_current != normalized_target_no_paren:
                continue
            
            # Skip if task was deleted
            if task_data['deleted']:
                continue
            
            # Determine task status
            status = 'OPEN'
            if task_data['completed']:
                # Check if it was reopened after completion
                latest_completion = max(task_data['completed'], key=lambda x: x['time'])
                latest_reopening = max(task_data['reopened'], key=lambda x: x['time']) if task_data['reopened'] else None
                
                if not latest_reopening or latest_reopening['time'] < latest_completion['time']:
                    status = 'COMPLETED'
            
            # Try to get actual task details from Google Tasks API
            task_details = None
            if creds:
                task_details = get_task_details_from_tasks_api(creds, task_id)
            
            # Find task name (extract from creation message or Tasks API)
            task_name = "Unknown Task"
            task_description = ""
            
            if task_details:
                task_name = task_details.get('title', 'Unknown Task')
                task_description = task_details.get('notes', '')
            elif task_data['created']['text']:
                # Fallback: Try to extract task name from the creation message
                text = task_data['created']['text']
                if "Created" in text and ":" in text:
                    task_name = text.split("Created")[1].split(":")[1].strip()
                    if "@" in task_name:
                        task_name = task_name.split("@")[0].strip()
                elif "Created" in text:
                    task_name = text.split("Created")[1].strip()
                    if "@" in task_name:
                        task_name = task_name.split("@")[0].strip()
            
            # Find due date (if any)
            due_date = None
            if task_details and task_details.get('due'):
                due_date = task_details.get('due')
            else:
                # Fallback: Look for due date mentions in updates
                for update in task_data['updates']:
                    if "due" in update['text'].lower() or "deadline" in update['text'].lower():
                        # Try to extract date from the message
                        # This is a simple implementation - could be enhanced with better date parsing
                        due_date = update['time']  # Use message time as fallback
            
            # Find last update time
            last_update = task_data['created']['time']
            if task_data['updates']:
                last_update = max(task_data['updates'], key=lambda x: x['time'])['time']
            if task_data['assigned']:
                latest_assignment = max(task_data['assigned'], key=lambda x: x['time'])
                if latest_assignment['time'] > last_update:
                    last_update = latest_assignment['time']
            if task_data['completed']:
                latest_completion = max(task_data['completed'], key=lambda x: x['time'])
                if latest_completion['time'] > last_update:
                    last_update = latest_completion['time']
            if task_data['reopened']:
                latest_reopening = max(task_data['reopened'], key=lambda x: x['time'])
                if latest_reopening['time'] > last_update:
                    last_update = latest_reopening['time']
            
            # Find last progress from the assignee
            last_progress = None
            assignee_updates = [update for update in task_data['updates'] 
                              if normalize_name(update['sender']) == normalize_name(assignee_name)]
            if assignee_updates:
                last_progress = max(assignee_updates, key=lambda x: x['time'])['time']
            
            # Find assignment time
            assignment_time = task_data['created']['time']
            if task_data['assigned']:
                # Find the most recent assignment to this person
                assignee_assignments = [a for a in task_data['assigned'] 
                                      if normalize_name(a['assignee']) == normalize_name(assignee_name)]
                if assignee_assignments:
                    assignment_time = max(assignee_assignments, key=lambda x: x['time'])['time']
            
            task_info = {
                'task_id': task_id,
                'task_name': task_name,
                'task_description': task_description,
                'assignee': current_assignee,
                'assignment_time': assignment_time,
                'due_date': due_date,
                'last_update': last_update,
                'last_progress': last_progress,
                'status': status,
                'space_name': space_name,
                'created_text': task_data['created']['text'],
                'created_sender': task_data['created']['sender'],
                'tasklist_title': task_details.get('tasklist_title', '') if task_details else '',
                'tasks_api_available': task_details is not None
            }
            
            tasks.append(task_info)
            
    except Exception as e:
        logging.error(f"Error fetching tasks for assignee from space {space_name}: {e}")
        raise
    
    return tasks

def export_messages(service, space_name: str, start_date: str, end_date: str, output_format: str = "json") -> None:
    """Export all chat messages from a specific space in the specified format."""
    logging.info(f"Exporting messages from space: {space_name}")
    
    try:
        # Get all messages from the specified space
        messages = get_messages_for_space(service, space_name, start_date, end_date)
        
        if not messages:
            logging.info("No messages found in the specified space and date range.")
            return
        
        # Format messages for export
        formatted_messages = []
        for message in messages:
            formatted_message = {
                'id': message.get('name', ''),
                'text': message.get('text', ''),
                'sender': message.get('sender', {}).get('displayName', 'Unknown'),
                'sender_id': message.get('sender', {}).get('name', ''),
                'space': space_name,
                'created_at': message.get('createTime', ''),
                'thread_name': message.get('thread', {}).get('name', ''),
                'message_type': message.get('messageType', ''),
                'deleted': message.get('deleted', False),
                'last_updated': message.get('lastUpdateTime', message.get('createTime', ''))
            }
            formatted_messages.append(formatted_message)
        
        # Generate filename with space name and date range
        start_iso = datetime.fromisoformat(start_date.replace('Z', '')).strftime('%Y-%m-%d')
        end_iso = datetime.fromisoformat(end_date.replace('Z', '')).strftime('%Y-%m-%d')
        space_display_name = space_name.split('/')[-1] if '/' in space_name else space_name
        
        if output_format.lower() == "csv":
            filename = f'messages_export_{space_display_name}_{start_iso}_{end_iso}.csv'
            save_data(formatted_messages, filename, "csv")
        else:  # Default to JSON
            filename = f'messages_export_{space_display_name}_{start_iso}_{end_iso}.json'
            save_data(formatted_messages, filename, "json")
            
    except Exception as e:
        logging.error(f"Error exporting messages from space {space_name}: {e}")
        raise

def list_spaces_interactive(spaces: List[Dict]) -> str:
    """List all spaces and let user choose one interactively."""
    print("\nAvailable spaces:")
    print("-" * 80)
    
    # Create a numbered list of spaces
    space_choices = []
    for i, space in enumerate(spaces, 1):
        space_id = space['name']
        display_name = space.get('displayName', space_id)
        space_type = space.get('spaceType', 'UNKNOWN')
        
        # Format space type for display
        if space_type == 'SPACE':
            type_indicator = '[PUBLIC]'
        elif space_type == 'DIRECT_MESSAGE':
            type_indicator = '[PRIVATE]'
        else:
            type_indicator = f'[{space_type}]'
        
        space_choices.append((i, space_id, display_name))
        print(f"{i:2d}. {type_indicator} {display_name}")
        print(f"    ID: {space_id}")
        print()
    
    # Get user choice
    while True:
        try:
            choice = input("Enter the number of the space to export messages from (or 'q' to quit): ").strip()
            
            if choice.lower() == 'q':
                print("Export cancelled.")
                return None
            
            choice_num = int(choice)
            if 1 <= choice_num <= len(space_choices):
                selected_space = space_choices[choice_num - 1][1]  # Get the space ID
                selected_name = space_choices[choice_num - 1][2]   # Get the display name
                print(f"\nSelected space: {selected_name}")
                return selected_space
            else:
                print(f"Please enter a number between 1 and {len(space_choices)}")
        except ValueError:
            print("Please enter a valid number or 'q' to quit")
        except KeyboardInterrupt:
            print("\nExport cancelled.")
            return None

def main():
    setup_logging()

    parser = argparse.ArgumentParser(description="Google Tasks Scrapper")
    subparsers = parser.add_subparsers(dest="command")

    # Config command for token management
    config_parser = subparsers.add_parser("config", help="Configure authentication token")

    # Spaces command
    spaces_parser = subparsers.add_parser("spaces", help="Retrieve a list of spaces")
    spaces_parser.add_argument("--json", action="store_true", help="Save the list of spaces to spaces.json file")
    spaces_parser.add_argument("--csv", action="store_true", help="Save the list of spaces to a CSV file")
    spaces_parser.add_argument("--include-direct-messages", action="store_true", help="Include direct message conversations")
    spaces_parser.add_argument("--all", action="store_true", help="Show all spaces including direct messages")

    # People command
    people_parser = subparsers.add_parser("people", help="Retrieve a list of people")
    people_parser.add_argument("--date-start", help="Start date in ISO format (e.g., 2022-01-15)")
    people_parser.add_argument("--date-end", help="End date in ISO format (e.g., 2022-01-15)")
    people_parser.add_argument("--past-month", action="store_true", help="Retrieve people from the past 30 days")
    people_parser.add_argument("--past-year", action="store_true", help="Retrieve people from the past 365 days")
    people_parser.add_argument("--json", action="store_true", help="Save the list of people to people.json file")
    people_parser.add_argument("--csv", action="store_true", help="Save the list of people to a CSV file")

    # Report command (previously Tasks)
    report_parser = subparsers.add_parser("report", help="Generate a tasks report")
    report_parser.add_argument("--date-start", help="Start date in ISO format (e.g., 2022-01-15)")
    report_parser.add_argument("--date-end", help="End date in ISO format (e.g., 2022-01-15)")
    report_parser.add_argument("--past-month", action="store_true", help="Generate report for the past 30 days")
    report_parser.add_argument("--past-year", action="store_true", help="Generate report for the past 365 days")
    report_parser.add_argument("--json", action="store_true", help="Save the report to task_report.json file")
    report_parser.add_argument("--csv", action="store_true", help="Save the report to a CSV file")

    # New Tasks command
    tasks_parser = subparsers.add_parser("tasks", help="Retrieve task information from spaces")
    tasks_parser.add_argument("--assignee", help="Filter tasks by assignee name (e.g., 'Priyanka D (P)')")
    tasks_parser.add_argument("--space", help="Space ID to search in (if not provided, will search all spaces)")
    tasks_parser.add_argument("--date-start", help="Start date in ISO format (e.g., 2022-01-15)")
    tasks_parser.add_argument("--date-end", help="End date in ISO format (e.g., 2022-01-15)")
    tasks_parser.add_argument("--past-day", action="store_true", help="Retrieve tasks from the past 1 day")
    tasks_parser.add_argument("--past-month", action="store_true", help="Retrieve tasks from the past 30 days")
    tasks_parser.add_argument("--past-year", action="store_true", help="Retrieve tasks from the past 365 days")
    tasks_parser.add_argument("--json", action="store_true", help="Save tasks to tasks.json file")
    tasks_parser.add_argument("--csv", action="store_true", help="Save tasks to CSV file")

    # Messages command
    messages_parser = subparsers.add_parser("messages", help="Export chat messages from spaces or direct messages")
    
    # Mutually exclusive group for space selection
    group = messages_parser.add_mutually_exclusive_group()
    group.add_argument("--space", help="Space ID to export from (if not provided, will show interactive selection)")
    group.add_argument("--all-spaces", action="store_true", help="Export messages from all public spaces")
    group.add_argument("--all-direct-messages", action="store_true", help="Export messages from all direct message conversations")
    group.add_argument("--all", action="store_true", help="Export messages from all spaces and direct messages")
    
    messages_parser.add_argument("--date-start", help="Start date in ISO format (e.g., 2022-01-15)")
    messages_parser.add_argument("--date-end", help="End date in ISO format (e.g., 2022-01-15)")
    messages_parser.add_argument("--past-month", action="store_true", help="Export messages from the past 30 days")
    messages_parser.add_argument("--past-year", action="store_true", help="Export messages from the past 365 days")
    messages_parser.add_argument("--json", action="store_true", help="Save the exported messages to JSON files (filename includes space name and date range)")
    messages_parser.add_argument("--csv", action="store_true", help="Save the exported messages to a CSV file")


    args = parser.parse_args()

    # If no command is provided, show help
    if not args.command:
        parser.print_help()
        print("\nAvailable commands:")
        print("  config   - Configure authentication token")
        print("  spaces   - Retrieve a list of spaces (use --help for options)")
        print("  people   - Retrieve a list of people")
        print("  report   - Generate a tasks report")
        print("  tasks    - Retrieve task information from spaces")
        print("  messages - Export chat messages from spaces or direct messages")
        print("\nUse --help with any command for more information.")
        return

    # Only get credentials when a command is actually provided
    if args.command == "config":
        print("Configuring authentication token...")
        creds = get_credentials()
        print("Authentication token configured successfully!")
        return

    # For all other commands, get credentials and build service
    creds = get_credentials()
    service = build('chat', 'v1', credentials=creds)

    if args.command == "spaces":
        if args.all:
            spaces = get_all_spaces_and_dms(service)
            logging.info("Retrieved all spaces including direct messages")
        elif args.include_direct_messages:
            public_spaces = get_public_spaces(service)
            dm_spaces = get_direct_message_spaces(service)
            spaces = public_spaces + dm_spaces
            logging.info("Retrieved public spaces and direct message conversations")
        else:
            spaces = get_spaces(service)
            logging.info("Retrieved public spaces only")
        
        if args.json:
            save_data(spaces, "spaces.json", "json")
        elif args.csv:
            save_data(spaces, "spaces.csv", "csv")
        else:
            # Output space ID and space name in format "space id : space name"
            for space in spaces:
                space_id = space['name']
                if space.get('spaceType') == 'DIRECT_MESSAGE':
                    space_name = "Direct Message"
                else:
                    space_name = space.get('displayName', space_id)
                print(f"{space_id} : {space_name}")

    elif args.command == "people":
        try:
            date_start, date_end = parse_date_range(args)
        except ValueError as e:
            logging.error(e)
            return

        spaces = load_from_json("spaces.json") or get_spaces(service)
        people = get_people(service, spaces, date_start, date_end)
        if args.json:
            save_data(people, "people.json", "json")
        elif args.csv:
            save_data(people, "people.csv", "csv")
        else:
            print(json.dumps(people, indent=4, ensure_ascii=False))

    elif args.command == "report":
        try:
            date_start, date_end = parse_date_range(args)
        except ValueError as e:
            logging.error(e)
            return

        # First try to load tasks from tasks.json
        tasks = load_from_json("tasks.json")
        
        if tasks:
            logging.info("Using existing tasks from tasks.json")
            all_tasks = tasks
        else:
            logging.info("No tasks.json found. Fetching tasks from API...")
            spaces = load_from_json("spaces.json") or get_spaces(service)
            people = load_from_json("people.json") or None

            # Fetch all tasks
            all_tasks = []
            for space in spaces:
                tasks = get_tasks(service, space['name'], date_start, date_end)
                all_tasks.extend(tasks)

            # Filter tasks if people.json exists
            if people:
                all_tasks = filter_tasks(all_tasks, people, [space['name'] for space in spaces])

        # Generate the report
        report = analyze_tasks(all_tasks)
        if args.json:
            # Convert report to list of dicts for JSON output
            report_dict = report.to_dict('records')
            save_data(report_dict, "task_report.json", "json")
            logging.info("Report saved as task_report.json")
        elif args.csv:
            generate_report(report, date_start, date_end)
        else:
            # Convert dates to ISO format for display
            start_iso = datetime.fromisoformat(date_start.replace('Z', '')).strftime('%Y-%m-%d')
            end_iso = datetime.fromisoformat(date_end.replace('Z', '')).strftime('%Y-%m-%d')
            print(f"\nTask Report for period: {start_iso} to {end_iso}")
            print(report.to_string(index=False))

    elif args.command == "tasks":
        try:
            date_start, date_end = parse_date_range(args)
        except ValueError as e:
            logging.error(e)
            return

        # Load spaces from file or fetch all spaces
        if args.space:
            # Search in specific space
            spaces = [{'name': args.space, 'displayName': args.space}]
        else:
            spaces = load_from_json("spaces.json") or get_spaces(service)
        
        if args.assignee:
            # Filter tasks by assignee
            assignee_name = args.assignee
            logging.info(f"Searching for tasks assigned to: {assignee_name}")

            all_tasks = []
            for space in spaces:
                space_name = space.get('displayName', space['name'])
                logging.info(f"Searching in space: {space_name}")
                
                try:
                    tasks = get_tasks_for_assignee(service, space['name'], assignee_name, date_start, date_end, creds)
                    all_tasks.extend(tasks)
                    logging.info(f"Found {len(tasks)} tasks for {assignee_name} in {space_name}")
                except Exception as e:
                    logging.error(f"Error searching tasks in space {space_name}: {e}")
                    continue

            if not all_tasks:
                logging.info(f"No tasks found assigned to {assignee_name}")
                return

            # Format output
            if args.json:
                filename = f"tasks_for_{assignee_name.replace(' ', '_').replace('(', '').replace(')', '')}.json"
                save_data(all_tasks, filename, "json")
                logging.info(f"Saved {len(all_tasks)} tasks to {filename}")
            elif args.csv:
                filename = f"tasks_for_{assignee_name.replace(' ', '_').replace('(', '').replace(')', '')}.csv"
                save_data(all_tasks, filename, "csv")
                logging.info(f"Saved {len(all_tasks)} tasks to {filename}")
            else:
                # Display results in terminal
                print(f"\nTasks assigned to {assignee_name}:")
                print("=" * 80)
                
                for i, task in enumerate(all_tasks, 1):
                    print(f"\n{i}. Task: {task['task_name']}")
                    if task['task_description']:
                        print(f"   Description: {task['task_description']}")
                    print(f"   ID: {task['task_id']}")
                    print(f"   Assignee: {task['assignee']}")
                    print(f"   Assignment Time: {task['assignment_time']}")
                    print(f"   Due Date: {task['due_date'] or 'Not specified'}")
                    print(f"   Last Update: {task['last_update']}")
                    print(f"   Last Progress from {assignee_name}: {task['last_progress'] or 'None'}")
                    print(f"   Status: {task['status']}")
                    print(f"   Space: {task['space_name']}")
                    if task.get('tasklist_title'):
                        print(f"   Task List: {task['tasklist_title']}")
                    print(f"   Tasks API: {'Available' if task.get('tasks_api_available') else 'Not Available'}")
                    print(f"   Created by: {task['created_sender']}")
                    print(f"   Creation Text: {task['created_text']}")
                    print("-" * 80)
                
                logging.info(f"Found {len(all_tasks)} tasks assigned to {assignee_name}")
        else:
            # Get all formatted tasks (original behavior)
            formatted_tasks = get_formatted_tasks(service, spaces, date_start, date_end)
            
            if args.json:
                save_data(formatted_tasks, "tasks.json", "json")
                logging.info(f"Saved {len(formatted_tasks)} tasks to tasks.json")
            elif args.csv:
                save_data(formatted_tasks, "tasks.csv", "csv")
                logging.info(f"Saved {len(formatted_tasks)} tasks to tasks.csv")
            else:
                print(json.dumps(formatted_tasks, indent=4, ensure_ascii=False))
                logging.info(f"Found {len(formatted_tasks)} tasks")

    elif args.command == "messages":
        try:
            date_start, date_end = parse_date_range(args)
        except ValueError as e:
            logging.error(e)
            return

        # Determine which spaces to export from
        if args.space:
            # Use the specified space (works for both public spaces and direct messages)
            space_name = args.space
            # Get all spaces to validate the specified space exists
            all_spaces = get_all_spaces_and_dms(service)
            space_exists = any(space['name'] == space_name for space in all_spaces)
            if not space_exists:
                logging.error(f"Space '{space_name}' not found. Use 'spaces' command to see available spaces.")
                return
            
            # Export from single space
            if args.json:
                export_messages(service, space_name, date_start, date_end, "json")
            elif args.csv:
                export_messages(service, space_name, date_start, date_end, "csv")
            else:
                # Just display the messages without saving
                messages = get_messages_for_space(service, space_name, date_start, date_end)
                if messages:
                    formatted_messages = []
                    for message in messages:
                        formatted_message = {
                            'id': message.get('name', ''),
                            'text': message.get('text', ''),
                            'sender': message.get('sender', {}).get('displayName', 'Unknown'),
                            'sender_id': message.get('sender', {}).get('name', ''),
                            'space': space_name,
                            'created_at': message.get('createTime', ''),
                            'thread_name': message.get('thread', {}).get('name', ''),
                            'message_type': message.get('messageType', ''),
                            'deleted': message.get('deleted', False),
                            'last_updated': message.get('lastUpdateTime', message.get('createTime', ''))
                        }
                        formatted_messages.append(formatted_message)
                    print(json.dumps(formatted_messages, indent=4, ensure_ascii=False))
                    logging.info(f"Found {len(formatted_messages)} messages (not saved)")
                else:
                    logging.info("No messages found in the specified space and date range.")
        
        elif args.all_spaces:
            # Export from all public spaces
            logging.info("Exporting messages from all public spaces...")
            spaces = get_public_spaces(service)
            for space in spaces:
                space_name = space['name']
                space_display = space.get('displayName', space_name)
                logging.info(f"Exporting from public space: {space_display}")
                if args.json:
                    export_messages(service, space_name, date_start, date_end, "json")
                elif args.csv:
                    export_messages(service, space_name, date_start, date_end, "csv")
                else:
                    logging.info(f"Would export from {space_display} (use --json or --csv to actually export)")
        
        elif args.all_direct_messages:
            # Export from all direct message conversations
            logging.info("Exporting messages from all direct message conversations...")
            spaces = get_direct_message_spaces(service)
            for space in spaces:
                space_name = space['name']
                space_display = space.get('displayName', space_name)
                logging.info(f"Exporting from direct message: {space_display}")
                if args.json:
                    export_messages(service, space_name, date_start, date_end, "json")
                elif args.csv:
                    export_messages(service, space_name, date_start, date_end, "csv")
                else:
                    logging.info(f"Would export from {space_display} (use --json or --csv to actually export)")
        
        elif args.all:
            # Export from all spaces and direct messages
            logging.info("Exporting messages from all spaces and direct messages...")
            spaces = get_all_spaces_and_dms(service)
            for space in spaces:
                space_name = space['name']
                space_display = space.get('displayName', space_name)
                space_type = space.get('spaceType', 'UNKNOWN')
                logging.info(f"Exporting from {space_type.lower()}: {space_display}")
                if args.json:
                    export_messages(service, space_name, date_start, date_end, "json")
                elif args.csv:
                    export_messages(service, space_name, date_start, date_end, "csv")
                else:
                    logging.info(f"Would export from {space_display} (use --json or --csv to actually export)")
        
        else:
            # Interactive space selection (current functionality)
            spaces = load_from_json("spaces.json") or get_spaces(service)
            space_name = list_spaces_interactive(spaces)
            if not space_name:
                return  # User cancelled
            
            # Export from selected space
            if args.json:
                export_messages(service, space_name, date_start, date_end, "json")
            elif args.csv:
                export_messages(service, space_name, date_start, date_end, "csv")
            else:
                # Just display the messages without saving
                messages = get_messages_for_space(service, space_name, date_start, date_end)
                if messages:
                    formatted_messages = []
                    for message in messages:
                        formatted_message = {
                            'id': message.get('name', ''),
                            'text': message.get('text', ''),
                            'sender': message.get('sender', {}).get('displayName', 'Unknown'),
                            'sender_id': message.get('sender', {}).get('name', ''),
                            'space': space_name,
                            'created_at': message.get('createTime', ''),
                            'thread_name': message.get('thread', {}).get('name', ''),
                            'message_type': message.get('messageType', ''),
                            'deleted': message.get('deleted', False),
                            'last_updated': message.get('lastUpdateTime', message.get('createTime', ''))
                        }
                        formatted_messages.append(formatted_message)
                    print(json.dumps(formatted_messages, indent=4, ensure_ascii=False))
                    logging.info(f"Found {len(formatted_messages)} messages (not saved)")
                else:
                    logging.info("No messages found in the specified space and date range.")

if __name__ == '__main__':
    main()

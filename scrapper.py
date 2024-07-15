import os
import logging
from datetime import datetime, timedelta
from typing import List, Dict
import calendar

import pandas as pd
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

# Constants
SCOPES = [
    'https://www.googleapis.com/auth/chat.spaces',
    'https://www.googleapis.com/auth/chat.messages',
    'https://www.googleapis.com/auth/chat.messages.readonly'
]
TOKEN_FILE = 'token.json'
CREDENTIALS_FILE = 'client_secret.json'


def setup_logging():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


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
            creds = flow.run_local_server(port=7276)
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())

    return creds


def get_spaces(service) -> List[Dict]:
    """Retrieve all spaces from Google Chat."""
    spaces = []
    page_token = None
    while True:
        response = service.spaces().list(pageToken=page_token).execute()
        spaces.extend(response.get('spaces', []))
        page_token = response.get('nextPageToken')
        if not page_token:
            break
    return spaces


def get_tasks(service, space_name: str, start_date: str, end_date: str) -> List[Dict]:
    """Retrieve tasks from a specific space within a date range."""
    tasks = []
    completed_tasks, reopened_tasks, deleted_tasks, assigned_tasks = set(), set(), set(), set()
    page_token = None

    while True:
        response = service.spaces().messages().list(
            parent=space_name,
            pageToken=page_token,
            filter=f'createTime > "{start_date}" AND createTime < "{end_date}"'
        ).execute()

        for message in response.get('messages', []):
            if 'via Tasks' in message.get('text', []):
                task_id = message['thread']['name'].split("/")[3]
                text = message['text']
                assignee = text.split("@")[1].split("(")[0] if "@" in text else "Unassigned"

                if "Created" in text:
                    tasks.append({
                        'id': task_id,
                        'assignee': assignee,
                        'status': 'OPEN',
                        'created_time': message['createTime']
                    })
                elif "Assigned" in text:
                    assigned_tasks.add(task_id + "@" + assignee)
                elif "Completed" in text:
                    completed_tasks.add(task_id)
                elif "Deleted" in text:
                    deleted_tasks.add(task_id)
                elif "Re-opened" in text:
                    reopened_tasks.add(task_id)

        page_token = response.get('nextPageToken')
        if not page_token:
            break

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
    """Analyze tasks and generate a report."""
    df = pd.DataFrame(tasks)
    total_tasks = df.groupby('assignee').size().rename('total_tasks')
    completed_tasks = df[df['status'] == 'COMPLETED'].groupby('assignee').size().rename('completed_tasks')
    report = pd.concat([total_tasks, completed_tasks], axis=1).fillna(0)
    report['completion_rate'] = report['completed_tasks'] / report['total_tasks']
    return report


def generate_report(report: pd.DataFrame, month: str, year: int):
    """Generate and save the task report as a CSV file."""
    file_name = f'task_report_{year}_{month}.csv'
    report.to_csv(file_name)
    logging.info(f"Report for {month}/{year} saved as {file_name}")
    logging.info(report)


def main():
    setup_logging()

    creds = get_credentials()
    service = build('chat', 'v1', credentials=creds)

    spaces = get_spaces(service)

    year = int(input("Enter the year for the report: "))
    init_month = int(input("Enter the initial month for the report (1-12): "))
    end_month = int(input("Enter the final month for the report (1-12): "))

    start_date = datetime(year, init_month, 1).isoformat() + "-04:00"
    end_date = (datetime(year, end_month + 1, 1) - timedelta(days=1)).isoformat() + "-04:00"

    all_tasks = []
    for space in spaces:
        logging.info(f"Getting tasks from space '{space['name']}'")
        tasks = get_tasks(service, space['name'], start_date, end_date)
        all_tasks.extend(tasks)

    report = analyze_tasks(all_tasks)

    # Get short month names
    init_month_name = calendar.month_abbr[init_month]
    end_month_name = calendar.month_abbr[end_month]
    month_range = f"{init_month_name}-{end_month_name}" if init_month != end_month else init_month_name

    generate_report(report, month_range, year)


if __name__ == '__main__':
    main()
"""
Flask Performance Dashboard for Google Spaces Tasks Reporter

This web application provides a performance management dashboard that displays
task metrics in a person x space matrix format. It wraps the existing CLI tool
functionality while preserving all command-line features.
"""

from flask import Flask, render_template, jsonify, request
from scrapper import (
    get_credentials, get_spaces, get_tasks,
    get_past_day_dates, get_past_week_dates, get_past_month_dates
)
from googleapiclient.discovery import build
import json
import os

app = Flask(__name__)

# Page route - renders empty page with Fetch Data button
@app.route('/')
def dashboard():
    """
    Render the dashboard page without loading data.
    User must click "Fetch Data" button to load from Google API.
    """
    # Get period from query parameter
    period = request.args.get('period', 'last-week')
    
    # Validate period
    if period not in ['last-day', 'last-week', 'last-month']:
        period = 'last-week'
    
    # Just pass period to template, NO API calls
    return render_template('dashboard.html', period=period)

# API endpoint to fetch data when user clicks "Fetch Data" button
@app.route('/api/fetch-data')
def fetch_data():
    """
    Fetch all task data from Google Chat API for the specified period.
    This is called only when user explicitly clicks the "Fetch Data" button.
    """
    # Get period from query parameter
    period = request.args.get('period', 'last-week')
    
    # Validate period
    if period not in ['last-day', 'last-week', 'last-month']:
        return jsonify({'error': 'Invalid period'}), 400
    
    try:
        # Get date range based on period
        date_funcs = {
            'last-day': get_past_day_dates,
            'last-week': get_past_week_dates,
            'last-month': get_past_month_dates
        }
        date_start, date_end = date_funcs[period]()
        
        # Fetch data from Google
        creds = get_credentials()
        service = build('chat', 'v1', credentials=creds)
        all_spaces = get_spaces(service)
        
        # Load space filtering config
        space_whitelist = []
        space_blacklist = []
        if os.path.exists('config.json'):
            try:
                with open('config.json', 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    space_whitelist = config.get('space_whitelist', [])
                    space_blacklist = config.get('space_blacklist', [])
            except Exception as e:
                print(f"Error loading config.json: {e}")
        
        # Filter spaces based on whitelist/blacklist
        # If whitelist is empty, all spaces are in scope (except blacklisted ones)
        # If whitelist is not empty, only whitelisted spaces are in scope (and not blacklisted)
        if space_whitelist:
            spaces = [s for s in all_spaces if s['name'] in space_whitelist and s['name'] not in space_blacklist]
        else:
            spaces = [s for s in all_spaces if s['name'] not in space_blacklist]
        
        # Fetch all tasks from filtered spaces
        all_tasks = []
        for space in spaces:
            try:
                tasks = get_tasks(service, space['name'], date_start, date_end, "context")
                all_tasks.extend(tasks)
            except Exception as e:
                # Log error but continue with other spaces
                print(f"Error fetching tasks from space {space['name']}: {e}")
                continue
        
        # Extract all unique people from tasks
        all_people = set()
        for task in all_tasks:
            assignee = task.get('assignee', '').strip()
            sender = task.get('sender', '').strip()
            if assignee and assignee not in ['Unassigned', 'Unknown']:
                all_people.add(assignee)
            if sender and sender != 'Unknown':
                all_people.add(sender)
        
        # Prepare data structure
        data = {
            'period': period,
            'date_start': date_start,
            'date_end': date_end,
            'all_people': sorted(list(all_people)),
            'spaces': spaces,
            'tasks': all_tasks
        }
        
        return jsonify(data)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)




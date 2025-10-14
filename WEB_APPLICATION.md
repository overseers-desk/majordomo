# Web Application Guide

This document explains how to use and configure the Google Spaces Tasks Reporter web application.

## Overview

The web application provides a visual performance dashboard that displays task metrics in a person × space matrix format. It allows you to:

- View task assignments, completions, and creations across multiple spaces
- Filter by people and spaces using interactive checkboxes
- Drill down into individual tasks by clicking on the matrix numbers
- Track performance over different time periods (last day, last week, last month)

## Running the Web Application

### Local Development

Start the Flask development server:

```bash
python3 app.py
```

The application will be available at `http://localhost:5000`

### CGI Deployment

For production deployment on a web server, refer to `CGI_DEPLOYMENT.md` for detailed instructions on deploying as a CGI application.

## Configuration

### Space Filtering (config.json)

The web application uses `config.json` to control which Google Chat spaces are included in the dashboard. This file contains two arrays:

```json
{
  "space_whitelist": [],
  "space_blacklist": ["spaces/AAAAMj0BPws"]
}
```

**Configuration Logic:**

- **Whitelist empty (default)**: All spaces are in scope, except those in the blacklist
- **Whitelist not empty**: Only whitelisted spaces are in scope, excluding any that are also blacklisted

**Example configurations:**

```json
// Include all spaces except Grayhat
{
  "space_whitelist": [],
  "space_blacklist": ["spaces/AAAAMj0BPws"]
}

// Only include specific spaces
{
  "space_whitelist": ["spaces/AAAAfPFB3gs", "spaces/AAAA1djitm8"],
  "space_blacklist": []
}

// Include specific spaces but exclude one
{
  "space_whitelist": ["spaces/AAAAfPFB3gs", "spaces/AAAA1djitm8", "spaces/AAAABDd8-KM"],
  "space_blacklist": ["spaces/AAAABDd8-KM"]
}
```

### Finding Space IDs

To discover which spaces are available and find their IDs for the configuration:

```bash
# List all spaces with their IDs and names
python3 scrapper.py spaces

# Save to a file for reference
python3 scrapper.py spaces --json spaces.json
python3 scrapper.py spaces --csv spaces.csv
```

The output shows space IDs (like `spaces/AAAAMj0BPws`) paired with their display names, making it easy to identify which spaces to whitelist or blacklist.

## Using the Dashboard

### 1. Select Time Period

Click on the time period tabs at the top:
- **Last Day**: Shows tasks from the past 24 hours
- **Last Week**: Shows tasks from the past 7 days (default)
- **Last Month**: Shows tasks from the past 30 days

### 2. Fetch Data

Click the "Fetch Data from Google" button to load data from the Google Chat API. This step is manual to avoid unnecessary API calls on every page load.

### 3. Filter View

After data loads, you'll see two filter sections:

**People Checkboxes**: Select which team members to include in the matrix
**Space Checkboxes**: Select which spaces to include in the matrix

Your selections are saved in browser cookies and will persist across sessions.

### 4. View Performance Matrix

The matrix displays three numbers for each person × space combination:

```
Assigned / Completed / Given
```

- **Assigned**: Tasks assigned to this person in this space
- **Completed**: Tasks this person completed in this space
- **Given**: Tasks this person created/assigned to others in this space

The rightmost column shows totals across all spaces for each person.
The bottom row shows totals across all people for each space.

### 5. View Task Details

Click any number in the matrix to see detailed task information, including:
- Task ID
- Creation time
- Assignee and sender
- Status (OPEN/COMPLETED)
- Space name
- First message in the thread (provides task context)

## Data Flow

1. **Configuration**: `config.json` filters which spaces to include
2. **API Fetch**: Data is fetched only from filtered spaces
3. **People Extraction**: All unique people are extracted from tasks in the filtered spaces
4. **Client Filtering**: Users can further filter the view using checkboxes

This approach ensures that blacklisted spaces never appear in the dashboard, and if you're using a whitelist, only those specific spaces are considered.

## Browser Preferences

The dashboard saves your checkbox selections in browser cookies:
- `tracked_people`: List of selected people
- `tracked_spaces`: List of selected spaces

These preferences persist for 30 days and are saved whenever you change checkbox selections.

## Troubleshooting

**No data appears after clicking "Fetch Data"**
- Check browser console for errors
- Verify `token.json` and `client_secret.json` are present
- Ensure you have permissions to access the Google Chat spaces
- Check that spaces aren't all blacklisted in `config.json`

**Some spaces are missing**
- Check `config.json` to ensure they're not blacklisted
- If using a whitelist, ensure they're included in the whitelist
- Verify you have access to those spaces in Google Chat

**Performance is slow**
- Consider blacklisting inactive or irrelevant spaces to reduce data fetching
- Use shorter time periods (Last Day instead of Last Month)
- The initial data fetch can take time with many spaces; this is normal

## API Rate Limits

The Google Chat API has rate limits. If you encounter rate limit errors:
- Reduce the time period scope
- Blacklist unused spaces to reduce API calls
- Wait a few minutes before retrying

## Security Considerations

- Keep `token.json` and `client_secret.json` secure and never commit them to version control
- The web application requires authentication credentials that grant access to your Google Chat data
- For CGI deployment, ensure proper file permissions (see `CGI_DEPLOYMENT.md`)


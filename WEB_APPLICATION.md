# Web Application Guide

This document explains how to use and configure the Google Spaces Tasks Reporter web application.

## Overview

The web application provides a visual performance dashboard that displays task metrics in a person × space matrix format. It allows you to:

- View task assignments, completions, and creations across multiple spaces
- Filter by people and spaces using interactive checkboxes
- Drill down into individual tasks by clicking on the matrix numbers
- Track performance over different time periods (last day, last week, 4 weeks)

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

### Space Filtering (.htaccess)

The web application uses Apache environment variables in `.htaccess` to control which Google Chat spaces are excluded from the dashboard. This approach allows you to add comments documenting which spaces are being filtered:

```apache
# Space filtering configuration for the web dashboard
# Format: JSON array of space IDs (without "spaces/" prefix)

# Grayhat - excluded space  
# spaces/AAAAfPFB3gs - another excluded space
SetEnv IGNORE_SPACES '["AAAAMj0BPws", "AAAAfPFB3gs"]'
```

**Configuration:**
- The `IGNORE_SPACES` environment variable contains a JSON array of space IDs
- Space IDs should be listed WITHOUT the `spaces/` prefix (just the ID part)
- You can add comments above to document what each space is

### Finding Space IDs

To discover which spaces are available and find their IDs for the configuration:

```bash
# List all spaces with their IDs and names
python3 scrapper.py spaces

# Save to a file for reference
python3 scrapper.py spaces --json spaces.json
python3 scrapper.py spaces --csv spaces.csv
```

The output shows space IDs (like `spaces/AAAAMj0BPws`) paired with their display names, making it easy to identify which spaces to exclude in `.htaccess`.

## Using the Dashboard

### 1. Select Time Period

Click on the time period tabs at the top:
- **Last Day**: Shows tasks from the past 24 hours
- **Last Week**: Shows tasks from the past 7 days (default)
- **4 Weeks**: Shows tasks from the past 28 days

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

1. **Configuration**: `.htaccess` IGNORE_SPACES variable filters which spaces to exclude
2. **API Fetch**: Data is fetched from all spaces except those in IGNORE_SPACES
3. **People Extraction**: All unique people are extracted from tasks in the filtered spaces
4. **Client Filtering**: Users can further filter the view using checkboxes

This approach ensures that blacklisted spaces never appear in the dashboard.

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
- Check that spaces aren't all excluded in `.htaccess` IGNORE_SPACES

**Some spaces are missing**
- Check `.htaccess` to ensure they're not in IGNORE_SPACES
- Verify you have access to those spaces in Google Chat

**Performance is slow**
- Consider excluding inactive or irrelevant spaces in `.htaccess` IGNORE_SPACES
- Use shorter time periods (Last Day instead of 4 Weeks)
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


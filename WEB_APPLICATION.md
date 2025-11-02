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

This section explains how to deploy the web application as a CGI application on a production web server.

#### Files for CGI Deployment

This is a Flask-based CGI application. All application files from the repository must be deployed to the server.

**Critical files that must be present:**
- Config files in the `config/` directory (see README.md for details)

These credential files are not in the repository and must be configured separately.

#### Deployment Steps

**1. Prepare Your Files Locally**

Ensure the CGI script is executable:

```bash
chmod +x tasks-reporter.cgi
```

**2. Check Server Python Version**

First, identify the Python installation on your server:

```bash
ssh username@yourserver.com "ls -la /opt/alt/python*/bin/python* 2>/dev/null | grep -E 'python3'"
```

Note the Python path (e.g., `/opt/alt/python38/bin/python3.8`)

**3. Configure Python Path in CGI Script**

Edit the first line of `tasks-reporter.cgi` to match your server's Python:

```python
#!/opt/alt/python38/bin/python3.8
```

**4. Deploy Files to Server**

Ensure all application files are present in your server's cgi-bin directory using your preferred deployment method.

Refer to the "Files for CGI Deployment" section above for critical files that must be present.

**5. Set File Permissions**

```bash
ssh username@yourserver.com "cd ~/public_html/cgi-bin && chmod +x tasks-reporter.cgi && chmod 644 *.py *.json *.txt && chmod 755 static templates config"
```

**6. Install pip (if not available)**

Most cPanel servers don't have pip pre-installed. Bootstrap it:

```bash
ssh username@yourserver.com "curl -sS https://bootstrap.pypa.io/pip/3.8/get-pip.py | /opt/alt/python38/bin/python3.8 - --user"
```

This installs pip to `~/.local/bin/pip3.8`

**7. Install Python Dependencies**

```bash
ssh username@yourserver.com "~/.local/bin/pip3.8 install --user -r ~/public_html/cgi-bin/requirements.txt"
```

This installs:
- Flask
- Google API Python Client
- Google Auth libraries

**8. Test the Installation**

Access your application:

```
https://yourdomain.com/cgi-bin/tasks-reporter.cgi?period=last-day
```

**⚠️ IMPORTANT - Testing with last 24 hours:**
- Always test with `?period=last-day` (last 24 hours) - it's much faster (seconds vs minutes)
- Testing with `last-week` or `4-weeks` can take several minutes due to Google API calls
- The API fetches data from all Google Spaces for the entire period

Other available periods:
```
https://yourdomain.com/cgi-bin/tasks-reporter.cgi?period=last-day    ← Use this for testing!
https://yourdomain.com/cgi-bin/tasks-reporter.cgi?period=last-week
https://yourdomain.com/cgi-bin/tasks-reporter.cgi?period=4-weeks
```

#### Updating the Deployment

To update your deployment after making code changes:

1. Ensure the updated files are present on the server
2. If `requirements.txt` changed, reinstall dependencies:
   ```bash
   ssh username@yourserver.com "~/.local/bin/pip3.8 install --user -r ~/public_html/cgi-bin/requirements.txt"
   ```
3. If file permissions need resetting:
   ```bash
   ssh username@yourserver.com "cd ~/public_html/cgi-bin && chmod +x tasks-reporter.cgi && chmod 644 *.py *.json *.txt && chmod 755 static templates config"
   ```
4. Test the updated deployment

#### URL Structure

Your application will respond to these URLs:

```
/cgi-bin/tasks-reporter.cgi                → Dashboard (last week)
/cgi-bin/tasks-reporter.cgi/last-day       → Dashboard (last 24 hours)
/cgi-bin/tasks-reporter.cgi/last-week      → Dashboard (last week)
/cgi-bin/tasks-reporter.cgi/4-weeks        → Dashboard (4 weeks)
/cgi-bin/tasks-reporter.cgi/api/fetch-data/last-week  → API endpoint
```

#### Python 3.8 Compatibility Note

This application is compatible with Python 3.6+ due to the use of `Tuple[str, str]` 
type hints from the `typing` module instead of the newer `tuple[str, str]` syntax 
(which only works in Python 3.9+).

If you see errors like `TypeError: 'type' object is not subscriptable`, ensure:
1. You're using `from typing import Tuple` 
2. Type hints use `Tuple[str, str]` not `tuple[str, str]`

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

### Log Configuration

Logging priority: `LOG_DIR` env var → `../logs` directory → console only

**CGI deployment:**
```bash
mkdir -p /home/username/logs
# In .htaccess:
SetEnv LOG_DIR /home/username/logs
```

Or create `../logs` relative to application (auto-detected). Keeps logs outside web-accessible directories.

### Finding Space IDs

To discover which spaces are available and find their IDs for the configuration:

```bash
# List all spaces with their IDs and names
python3 google_chat_reporter.py spaces

# Save to a file for reference
python3 google_chat_reporter.py spaces --json spaces.json
python3 google_chat_reporter.py spaces --csv spaces.csv
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

### General Issues

**No data appears after clicking "Fetch Data"**
- Check browser console for errors
- Verify config files are present (see README.md for locations)
- Ensure you have permissions to access the Google Chat spaces
- Check that spaces aren't all excluded in `.htaccess` IGNORE_SPACES

**Some spaces are missing**
- Check `.htaccess` to ensure they're not in IGNORE_SPACES
- Verify you have access to those spaces in Google Chat

**Performance is slow**
- Consider excluding inactive or irrelevant spaces in `.htaccess` IGNORE_SPACES
- Use shorter time periods (Last Day instead of 4 Weeks)
- The initial data fetch can take time with many spaces; this is normal

### CGI Deployment Issues

**500 Internal Server Error**

1. **Check Python interpreter:**
   ```bash
   ssh username@server "cd ~/public_html/cgi-bin && ./tasks-reporter.cgi 2>&1 | head -20"
   ```
   Should output HTTP headers. If error, check shebang and imports.

2. **Check error logs:**
   Look for Apache/LiteSpeed error logs (server-specific)

3. **Check file permissions:**
   - tasks-reporter.cgi must be executable (755)
   - All Python files must be readable (644)

4. **Check Python dependencies:**
   ```bash
   ssh username@server "/opt/alt/python38/bin/python3.8 -c 'import flask; print(flask.__version__)'"
   ```

**Static Files Not Loading**

Static files are served through Flask, so if they don't load:

1. Check that `static/` directory is in the same location as `tasks-reporter.cgi`
2. Verify Flask is routing `/static/` correctly
3. Test static file directly: `https://yourdomain.com/cgi-bin/tasks-reporter.cgi/static/style.css`

**Authentication Issues**

If OAuth fails:

1. Ensure config files were generated locally first (see README.md)
2. Ensure the generated config files are present on the server in the `config/` directory
3. Check file permissions (should be 644 and readable by web server user)
4. Google OAuth tokens may expire - regenerate if needed

**Performance Issues with CGI**

CGI spawns a new process for each request, which means:

- Each request loads Python, Flask, and all libraries (~2-5 seconds)
- Google API authentication happens each time
- For single-user low-traffic use, this is acceptable
- For better performance, consider WSGI deployment

## API Rate Limits

The Google Chat API has rate limits. If you encounter rate limit errors:
- Reduce the time period scope
- Blacklist unused spaces to reduce API calls
- Wait a few minutes before retrying

## Security Considerations

- Keep config files secure and never commit them to version control (see README.md)
- The web application requires authentication credentials that grant access to your Google Chat data
- Always access via HTTPS to protect OAuth tokens in transit

### CGI Deployment Security

`.htaccess` protects sensitive files:
```apache
# Deny access to Python source files
<FilesMatch "\.(py)$">
    Require all denied
</FilesMatch>

# Protect config directory and all its contents
# Use Files directive to deny all files in config directory
<Files "config/*">
    Require all denied
</Files>
```

**Test protection** (must fail):
```
https://yourdomain.com/cgi-bin/config/token.json
https://yourdomain.com/app.py
```

If accessible, check: Apache has `AllowOverride All`, `.htaccess` is in same directory (644 permissions).

**File permissions:**
```bash
chmod 755 tasks-reporter.cgi   # Executable CGI script
chmod 644 *.py *.json *.txt    # Readable but not executable
chmod 755 static templates config     # Directories must be executable to list contents
```


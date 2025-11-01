# CGI Deployment Guide

This document explains how to deploy the Google Spaces Tasks Reporter web application as a CGI application.

For information about using and configuring the web application, see `WEB_APPLICATION.md`.

## Files for CGI Deployment

This is a Flask-based CGI application. All application files from the repository must be deployed to the server.

**Critical files that must be present:**
- **token.json** - Google OAuth credentials (must be generated first using `python3 google_chat_reporter.py config`)
- **client_secret.json** - Google API credentials

These credential files are not in the repository and must be configured separately.

## Deployment Steps

### 1. Prepare Your Files Locally

Ensure the CGI script is executable:

```bash
chmod +x tasks-reporter.cgi
```

### 2. Check Server Python Version

First, identify the Python installation on your server:

```bash
ssh username@yourserver.com "ls -la /opt/alt/python*/bin/python* 2>/dev/null | grep -E 'python3'"
```

Note the Python path (e.g., `/opt/alt/python38/bin/python3.8`)

### 3. Configure Python Path in CGI Script

Edit the first line of `tasks-reporter.cgi` to match your server's Python:

```python
#!/opt/alt/python38/bin/python3.8
```

### 4. Deploy Files to Server

Ensure all application files are present in your server's cgi-bin directory using your preferred deployment method.

Refer to the "Files for CGI Deployment" section above for critical files that must be present.

### 5. Set File Permissions

```bash
ssh username@yourserver.com "cd ~/public_html/cgi-bin && chmod +x tasks-reporter.cgi && chmod 644 *.py *.json *.txt && chmod 755 static templates"
```

### 6. Install pip (if not available)

Most cPanel servers don't have pip pre-installed. Bootstrap it:

```bash
ssh username@yourserver.com "curl -sS https://bootstrap.pypa.io/pip/3.8/get-pip.py | /opt/alt/python38/bin/python3.8 - --user"
```

This installs pip to `~/.local/bin/pip3.8`

### 7. Install Python Dependencies

```bash
ssh username@yourserver.com "~/.local/bin/pip3.8 install --user -r ~/public_html/cgi-bin/requirements.txt"
```

This installs:
- Flask
- Google API Python Client
- Google Auth libraries

### 8. Test the Installation

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

## Updating the Deployment

To update your deployment after making code changes:

1. Ensure the updated files are present on the server
2. If `requirements.txt` changed, reinstall dependencies:
   ```bash
   ssh username@yourserver.com "~/.local/bin/pip3.8 install --user -r ~/public_html/cgi-bin/requirements.txt"
   ```
3. If file permissions need resetting:
   ```bash
   ssh username@yourserver.com "cd ~/public_html/cgi-bin && chmod +x tasks-reporter.cgi && chmod 644 *.py *.json *.txt && chmod 755 static templates"
   ```
4. Test the updated deployment

## Troubleshooting

### 500 Internal Server Error

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

### Static Files Not Loading

Static files are served through Flask, so if they don't load:

1. Check that `static/` directory is in the same location as `tasks-reporter.cgi`
2. Verify Flask is routing `/static/` correctly
3. Test static file directly: `https://yourdomain.com/cgi-bin/tasks-reporter.cgi/static/style.css`

### Authentication Issues

If OAuth fails:

1. Ensure `token.json` was generated locally first:
   ```bash
   python3 google_chat_reporter.py config
   ```
2. Ensure the generated `token.json` is present on the server
3. Check file permissions (should be 644 and readable by web server user)
4. Google OAuth tokens may expire - regenerate if needed

### Performance Issues

CGI spawns a new process for each request, which means:

- Each request loads Python, Flask, and all libraries (~2-5 seconds)
- Google API authentication happens each time
- For single-user low-traffic use, this is acceptable
- For better performance, consider WSGI deployment (see DEPLOYMENT.md)

## URL Structure

Your application will respond to these URLs:

```
/cgi-bin/tasks-reporter.cgi                → Dashboard (last week)
/cgi-bin/tasks-reporter.cgi/last-day       → Dashboard (last 24 hours)
/cgi-bin/tasks-reporter.cgi/last-week      → Dashboard (last week)
/cgi-bin/tasks-reporter.cgi/4-weeks        → Dashboard (4 weeks)
/cgi-bin/tasks-reporter.cgi/api/fetch-data/last-week  → API endpoint
```

## Security Considerations

1. **Protect sensitive files:**
   JSON files (token.json, client_secret.json) should have restricted permissions (644)
   but they're in cgi-bin which typically isn't directly web-accessible anyway

2. **Use HTTPS:**
   Always access via HTTPS to protect OAuth tokens in transit

3. **File permissions summary:**
   ```bash
   chmod 755 tasks-reporter.cgi   # Executable CGI script
   chmod 644 *.py *.json *.txt    # Readable but not executable
   chmod 755 static templates     # Directories must be executable to list contents
   ```

## Python 3.8 Compatibility Note

This application is compatible with Python 3.6+ due to the use of `Tuple[str, str]` 
type hints from the `typing` module instead of the newer `tuple[str, str]` syntax 
(which only works in Python 3.9+).

If you see errors like `TypeError: 'type' object is not subscriptable`, ensure:
1. You're using `from typing import Tuple` 
2. Type hints use `Tuple[str, str]` not `tuple[str, str]`


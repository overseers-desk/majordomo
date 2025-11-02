# Configuration Directory

This directory contains authentication credentials and configuration files for the Google Spaces Tasks Reporter.

## Required Files

1. **client_secret.json** - OAuth 2.0 client credentials from Google Cloud Console
   - Download from Google Cloud Console > APIs & Services > Credentials
   - Select your OAuth 2.0 client and download JSON
   - Save as `config/client_secret.json`

2. **token.json** - Generated user authorization token
   - Created automatically by running: `python3 google_chat_reporter.py config`
   - Contains OAuth access and refresh tokens
   - Regenerate if authentication fails

## Setup Instructions

1. Download OAuth credentials from Google Cloud Console
2. Save as `config/client_secret.json`
3. Run: `python3 google_chat_reporter.py config`
4. This generates `config/token.json` with your authorization

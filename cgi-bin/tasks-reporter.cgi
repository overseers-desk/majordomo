#!/opt/alt/python38/bin/python3.8
"""
CGI Script for Google Spaces Tasks Reporter Flask Application

This script adapts the Flask app to run as a CGI script.
For CGI deployment, this file should be:
1. Placed in your web server's cgi-bin directory
2. Made executable (chmod +x tasks-reporter.cgi)
3. Have correct shebang pointing to your Python 3 interpreter
"""

import sys
import os
from wsgiref.handlers import CGIHandler

# Add the application directory to the Python path
# This assumes tasks-reporter.cgi is in cgi-bin/ subdirectory and app.py is in parent directory
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)
sys.path.insert(0, parent_dir)
os.chdir(parent_dir)

# Import the Flask app
from app import app

# Run the Flask app through CGI
if __name__ == '__main__':
    CGIHandler().run(app)


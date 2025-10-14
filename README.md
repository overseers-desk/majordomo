# Google Spaces Tasks Reporter

This script creates comprehensive efficiency reports by analysing Google Chat spaces and extracting task information to generate detailed completion statistics. The tool retrieves task status and assignee data across specified time ranges, calculating metrics such as tasks received, tasks completed, and completion rates. All reports are exported to CSV format for further analysis and reporting.

## Overview

The Google Spaces Tasks Reporter is designed for teams and organisations that need to track productivity and task completion across Google Chat spaces. By leveraging the Google Tasks API and Google Chat API, it provides insights into team performance and task management efficiency through automated data collection and reporting.

**Important**: This tool requires a Google Workspace account (Business or Enterprise) for full functionality. Personal Gmail accounts cannot access the Google Chat API, which is essential for retrieving space and task information. If you're using a personal Gmail account, you'll need to upgrade to Google Workspace or work with your organisation's administrator to gain access.

## Prerequisites and Setup

The script requires Python 3.12 and access to Google Cloud services. Before installation, you'll need to configure several Google Cloud components to enable the necessary APIs and authentication.

### Google Cloud Console Configuration

Begin by creating a project in the [Google Cloud Console](https://console.cloud.google.com/) if you don't already have one. Once your project is established, navigate to the APIs & Services section to enable the required services. You'll need to activate both the Google Tasks API and Google Chat API, along with the People API for comprehensive functionality.

### OAuth2 Client Setup

Create an OAuth2 client within your project, selecting the "Desktop" application type for optimal compatibility. When naming the client, "Google-Spaces-Tasks-reporter" works well. In the Credentials section, ensure you add `http://localhost:7276/` to the authorised redirect URIs to enable local authentication.

Download the generated JSON credential file and rename it to `client_secret.json`, placing it in your project's root directory. This file contains the necessary authentication credentials for the script to interact with Google's services.

### Google Chat App Configuration

Google requires every project using the Chat API to have a configured Chat app. Navigate to the Chat API Configuration page within your project settings. When setting up the app, use "Google-Spaces-Tasks" as the name (keeping it concise) and provide your GitHub repository URL. Importantly, disable interactive features since this application operates passively without user interaction.

## Installation

The script offers two installation methods depending on your system preferences and requirements.

### Ubuntu/Debian System Packages (Recommended)

For Ubuntu and Debian systems, the recommended approach uses system packages for better integration and dependency management:

```bash
sudo apt update
sudo apt install python3-googleapi python3-google-auth python3-google-auth-oauthlib python3-httplib2 python3-requests
```

This method eliminates the need for the `requirements.txt` file and provides system-level package management.

### Python pip Packages

Alternatively, you can install all dependencies using pip:

```bash
pip install -r requirements.txt
```

This approach is suitable for environments where system packages aren't available or when you prefer Python-specific package management.

## Usage

The script provides a command-line interface with several subcommands for different operations. When run without any arguments, it displays a helpful overview of available commands.

### Command Overview

```bash
python3 scrapper.py [command] [options]
```

**Available Commands:**
- `config` - Configure authentication token
- `spaces` - Retrieve a list of spaces
- `people` - Retrieve a list of people
- `report` - Generate a tasks report
- `tasks` - Retrieve task information from spaces
- `messages` - Export chat messages from a specific space

### Initial Setup

Before using the script, you'll need to configure your authentication token:

```bash
python3 scrapper.py config
```

This command will prompt for Google account authentication and generate user credentials stored in `token.json`. This file enables subsequent runs without repeated authentication prompts.

### Core Commands

**Config Command**: Sets up or refreshes your Google API authentication token. This is required for first-time use and when tokens expire.

**Spaces Command**: Retrieves a comprehensive list of Google Chat spaces accessible to your account. Use the `--json` flag to persist the results to `spaces.json` or `--csv` to save as `spaces.csv` for future reference.

**People Command**: Extracts information about individuals found within the specified spaces. This command supports comprehensive date filtering through `--date-start` and `--date-end` parameters in ISO format (YYYY-MM-DD), as well as convenient options for `--past-week` (7 days ago to today), `--past-month` (30 days ago to today) and `--past-year` (365 days ago to today). Results can be optionally saved to `people.json` using `--json` or `people.csv` using `--csv`.

**Tasks Command**: Collects detailed task information from spaces, including status, assignee, and completion details. This command supports comprehensive date filtering through `--date-start` and `--date-end` parameters in ISO format (YYYY-MM-DD), as well as convenient options for `--past-week` (7 days ago to today), `--past-month` (30 days ago to today) and `--past-year` (365 days ago to today). Results can be saved to `tasks.json` using `--json`.

**Report Command**: Generates comprehensive task completion reports with filtering and detailed view options. Supports date filtering (`--past-week`, `--past-month`, `--past-year`, or custom dates) and exports to CSV or JSON format.

Advanced filtering options:
- **`--assignee PATTERN`**: Filter by assignee using glob patterns (`*` = any characters, `?` = single character). Case-insensitive and Unicode-aware. Examples: `"*Edwards"` matches anyone ending with Edwards; `"Priyanka*"` matches anyone starting with Priyanka.
- **`--drill-down`**: Drills down into per-assignee details including actual task descriptions (first thread message), tasks assigned/closed in the past week, and completion status.

**Messages Command**: Exports all chat messages from a specific Google Chat space in either JSON or CSV format. This command can accept a `--space` parameter to specify the target space directly, or if no space is specified, it will present an interactive list of all available spaces for the user to choose from. The export includes comprehensive message details such as message ID, full text content, sender information, space name, creation time, last update time, thread details, message type, and deletion status. Use the `--json` flag to save the results to a JSON file or `--csv` to save as CSV; without either flag, messages are displayed in the terminal. When saving, output files are automatically named with the format `messages_export_{space_name}_{start_date}_{end_date}.{format}`. The command supports efficient date filtering using Google's API with options for `--past-week` (7 days ago to today), `--past-month` (30 days ago to today) and `--past-year` (365 days ago to today), or custom date ranges with `--date-start` and `--date-end`.

### Usage Examples

#### Initial Setup
```bash
python3 scrapper.py config            # Configure authentication (first time)
python3 scrapper.py spaces --json     # Get spaces list and save for reuse
```

#### Basic Reports
```bash
python3 scrapper.py report --past-week       # Standard weekly report
python3 scrapper.py report --past-month      # Monthly report
python3 scrapper.py report --csv report.csv  # Save to CSV
```

#### Filtered Reports
```bash
# Filter by name pattern
python3 scrapper.py report --assignee "*Edwards" --past-month
python3 scrapper.py report --assignee "John*" --past-week

# Drill down into per-person details
python3 scrapper.py report --past-week --drill-down

# Combined: filtered + drill-down
python3 scrapper.py report --assignee "*ÐS" --drill-down --past-month
```

#### Task Queries
```bash
python3 scrapper.py tasks --past-week --json           # Get recent tasks
python3 scrapper.py tasks --assignee "John*" --csv     # Filter by assignee
```

#### Message Export
```bash
python3 scrapper.py messages --space "spaces/ABC123" --past-week --json
python3 scrapper.py messages --all-spaces --csv        # All spaces
python3 scrapper.py messages                           # Interactive selection
```

#### Advanced Usage
```bash
# Custom date range
python3 scrapper.py report --date-start 2024-01-01 --date-end 2024-01-31 --csv

# Drill-down report with JSON export
python3 scrapper.py report --assignee "Team*" --drill-down --json detailed.json
```

Use `--help` with any command for detailed parameter information.

### Date Range Handling

The script provides flexible date range options across all relevant commands. When no date range is specified, the script defaults to analysing the previous calendar month. All dates should be provided in ISO format (YYYY-MM-DD) for consistency and accuracy.

**Available Date Range Options:**
- **Custom Range**: Use `--date-start` and `--date-end` to specify exact start and end dates
- **Past Week**: Use `--past-week` to analyse data from the past 7 days (from today)
- **Past Month**: Use `--past-month` to analyse data from the past 30 days (from today)
- **Past Year**: Use `--past-year` to analyse data from the past 365 days (from today)
- **Default**: When no options are specified, the script automatically uses the previous calendar month

**Commands with Date Range Support:**
- `people` - Extract people information with date filtering
- `tasks` - Retrieve task information with date filtering
- `report` - Generate task reports with date filtering
- `messages` - Export messages with date filtering

These date filtering options enable focused analysis of specific time periods, making it ideal for weekly reporting, monthly reporting, quarterly reviews, or targeted performance analysis. The `--past-week`, `--past-month` and `--past-year` options are particularly useful for quick analysis of recent activity without needing to calculate specific dates.

## Output and Data Management

The script generates several output files to support different analysis needs. The `spaces.json` file contains the complete list of accessible Google Chat spaces, while `people.json` stores information about individuals found within those spaces. The `tasks.json` file maintains detailed task records, and the CSV report provides aggregated completion statistics for the specified time period.

**Output Format Options:**
- **JSON format** (`--json`): Available for all commands, preserves the complete data structure including nested objects and arrays
- **CSV format** (`--csv`): Available for commands with flat data structures (spaces, people, report, messages). Note that complex nested data from tasks command is not suitable for CSV export due to multi-level structure.

These output files enable both immediate analysis and long-term data tracking, supporting various reporting requirements from quick status checks to comprehensive performance reviews. The CSV format ensures compatibility with spreadsheet applications and business intelligence tools for further analysis and visualisation.

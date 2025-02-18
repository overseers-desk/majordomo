# Google Spaces Tasks Reporter

This script was made with the idea of creating an efficiency report, using the Tasks status and assignee to create a
complete report that includes the amount of tasks received, amount of tasks completed and completion rate. You can
create the reports using a time range. The final report is saved to a `.csv` file.

## Installing
The script was tested and coded under Python 3.12. To start using the script, follow these steps:
- Create a Project on [Google Cloud Console](https://console.cloud.google.com/) if you don't have one yet.
- Enable Google Tasks API in your project. You can find it in APIs & Services section.
- Create a OAuth2 credential. You can find Credentials section in APIs & Services section. Make sure to add `http://localhost:7276/` to **Authorized redirect URIs**.
- Download the .json file from the created credential, rename it to `client_secret.json` and paste it in the root project folder.
- Install all the requirements, using pip. Reference command:
```bash
pip install -r requirements.txt
```

## How to Use
The first run will ask for login and generate the required user credentials, under the file `token.json`.

```
Google Tasks Scrapper

positional arguments:
  {spaces,people,tasks,report}
    spaces              Retrieve a list of spaces
    people              Retrieve a list of people
    tasks              Retrieve task information from spaces
    report             Generate a tasks report

options:
  -h, --help            show this help message and exit
```

### Spaces Command
```
usage: scrapper.py spaces [-h] [--save]

options:
  -h, --help  show this help message and exit
  --save      Save the list of spaces to a JSON file
```

### People Command
```
usage: scrapper.py people [-h] [--date-start DATE_START] [--date-end DATE_END] [--save]

options:
  -h, --help            show this help message and exit
  --date-start DATE_START
                        Start date in ISO format (e.g., 2022-01-15)
  --date-end DATE_END   End date in ISO format (e.g., 2022-01-15)
  --save                Save the list of people to a JSON file
```

### Tasks Command
```
usage: scrapper.py tasks [-h] [--date-start DATE_START] [--date-end DATE_END] [--save]

options:
  -h, --help            show this help message and exit
  --date-start DATE_START
                        Start date in ISO format (e.g., 2022-01-15)
  --date-end DATE_END   End date in ISO format (e.g., 2022-01-15)
  --save                Save tasks to tasks.json file
```

### Report Command
```
usage: scrapper.py report [-h] [--date-start DATE_START] [--date-end DATE_END] [--save]

options:
  -h, --help            show this help message and exit
  --date-start DATE_START
                        Start date in ISO format (e.g., 2022-01-15)
  --date-end DATE_END   End date in ISO format (e.g., 2022-01-15)
  --save                Save the report to a CSV file
```

## Output Files
- `spaces.json`: Contains the list of Google Chat spaces
- `people.json`: Contains the list of people found in the spaces
- `tasks.json`: Contains the list of tasks with their details
- `task_report_YYYY-MM-DD_YYYY-MM-DD.csv`: Contains the task completion report for the specified date range

## Date Ranges
If no date range is specified, the script will default to the previous calendar month. Dates should be provided in ISO format (YYYY-MM-DD).

# Google Spaces Tasks reporter

This is script was made with the idea of creating an efficiency report, using the Tasks status and asignee to create a
complete report that includes the amount of tasks received, amount of tasks completed and completion rate. You can
create the reports using a time range. The final report is saved to a ``.csv`` file.

## Installing
The script was tested and coded under Python 3.12. To start using the script, follow this steps:
- Create a Project on [Google Cloud Console](https://console.cloud.google.com/) if you don't have one yet.
- Enable Google Tasks API in your project. You can find it in APIs & Services section.
- Create a OAuth2 credential. You can find Credentials section in APIs & Services section. Make sure to add ``http://localhost:7276/`` to **Authorized redirect URIs**.
- Download the .json file from the created credential, rename it to ``client_secret.json`` and paste it in the root project folder.
- Install all the requirements, using pip. Reference command:
```pip install -r requirements.txt```

## How to use
The first run, will ask for login and generate the required user credentials, under the file ``tokens.json``.

```
Google Tasks Scrapper

positional arguments:
  {spaces,people,tasks}
    spaces              Retrieve a list of spaces
    people              Retrieve a list of people
    tasks               Retrieve a list of tasks

options:
  -h, --help            show this help message and exit
```

```
usage: scrapper.py spaces [-h] [--save]

options:
  -h, --help  show this help message and exit
  --save      Save the list of spaces to a JSON file
```

```
usage: scrapper.py people [-h] [--date-start DATE_START] [--date-end DATE_END] [--save]

options:
  -h, --help            show this help message and exit
  --date-start DATE_START
                        Start date in ISO format (e.g., 2022-01-15)
  --date-end DATE_END   End date in ISO format (e.g., 2022-01-15)
  --save                Save the list of people to a JSON file
```

```
usage: scrapper.py tasks [-h] [--date-start DATE_START] [--date-end DATE_END] [--save]

options:
  -h, --help            show this help message and exit
  --date-start DATE_START
                        Start date in ISO format (e.g., 2022-01-15)
  --date-end DATE_END   End date in ISO format (e.g., 2022-01-15)
  --save                Save the list of tasks to a CSV file
```



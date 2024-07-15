# Google Spaces Tasks reporter

This is script was made with the idea of creating an efficiency report, using the Tasks status and asignee to create a
complete report that includes the amount of tasks received, amount of tasks completed and completion rate. You can
create the reports using a time range. The final report is saved to a ``.csv`` file.

## How to Use

The script was tested and coded under Python 3.12. To start using the script, follow this steps:
- Create a Project on [Google Cloud Console](https://console.cloud.google.com/) if you don't have one yet.
- Enable Google Tasks API in your project. You can find it in APIs & Services section.
- Create a OAuth2 credential. You can find Credentials section in APIs & Services section. Make sure to add ``http://localhost:7276/`` to **Authorized redirect URIs**.
- Download the .json file from the created credential, rename it to ``credentials.json`` and paste it in the root project folder.
- Install all the requirements, using pip. Reference command:
```pip install -r requirements.txt```
  Or, for Ubuntu users:
```sudo apt-get install python3-googleapi python3-google-auth-oauthlib python3-pandas python3-google-auth```
- Run ``scrapper.py`` once. It'll prompt you to login through your Google Account. Select the account that has access to the spaces you want to analyze. At the moment, it'll analyze *all* spaces available to the account. Reference command: ```python scrapper.py```. Once you log in, it'll save your credentials under ``token.json`` file and proceed to create the report. For future runs it will not ask your login again.

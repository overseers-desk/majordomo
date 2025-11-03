# Estimated Directory Structure After Refactoring

```
/home/weiwu/code/Google-Spaces-Tasks-reporter.web/
│
├── bots/                           # was: chatbot/
│   ├── __init__.py                 # code to common bot routines, such as logging
│   ├── tachy.py                    # code specific to a bot tachy.py
│   └── raven/                      # code specific to bot raven
│       └── __init__.py
│
├── dispatcher                      # if user not configured dispatcher, it's not used.
│   └── __init__.py                 # its function should also include create_subscription.py, meaning it is callable from cli, so we can delete toplevel create_subscription.py
│
├── cgi-bin/
│   ├── chatbot.cgi                 # was: google_chat_app.cgi, note it is called using https://example.com/cgi-bin/chatbot.cgi/tachy
│   ├── dispatcher.cgi              # was: chat_events.cgi
│   └── tasks-reporter.cgi
│
├── config/                         # mostly unchanged, but adds new files
│   ├── client_secret.json
│   ├── token.json
│   ├── dispatcher.json             # anything hard coded in the current code such as subscription ID
│   └── bots/     
│       ├── tachy/
│       └── raven/
│           └── deepseek.json       # api-key to access bot-raven (example)
├── docs/
│   ├── CHATBOT-SETUP.md            # was: chatbot/README.md
│   └── DISPATCHER-SETUP.md         # was: chatbot/EVENTS_API_SETUP.md
│
├── logs/
├── static/
├── templates/
└── [root files unchanged]
```

**Changes:**
- logs are written to (when lack environmental variable) ../logs/google-chatbot.log (both the chatbots and the dispatcher writes to it)
- `chatbot/handler.py` → `bots/tachy.py`
- `google_chat_app.cgi` → `chatbot.cgi` (uses path-based routing: `/chatbot.cgi/tachy`)
- `chat_events.cgi` → `dispatcher.cgi`
- `chatbot/README.md` → `docs/CHATBOT-SETUP.md`
- `chatbot/EVENTS_API_SETUP.md` → `docs/DISPATCHER-SETUP.md`
- `create_subscription.py` → functionality moved to `dispatcher/__init__.py` (CLI callable)
- Add `dispatcher/` package and `dispatcher.cgi` (orchestrated mode only)
- Add `config/dispatcher.json` (subscription IDs, project/topic config)
- Add `config/bots/tachy/` and `config/bots/raven/` directories for bot-specific configs (API keys, etc.)

**Removed after refactoring:**
- `chatbot/` (entire folder - all content moved to `bots/` and `docs/`)
- `create_subscription.py` (functionality in `dispatcher/__init__.py`)
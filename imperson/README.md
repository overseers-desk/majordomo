# imperson

imperson answers Google Chat direct messages as an account. It signs in to Chat as that account, through majordomo and the account's own login. Each conversation has its own Claude Agent SDK session, resumed every time the person writes again, so the agent remembers what was said. Its abilities are skills. Claude picks one from what the person writes:

- **reimbursement**: someone sends an invoice or receipt and is paid back within a weekly allowance. A person in the accounts space is tasked with the payment.
- **weather**: someone asks about the weather.
- **events**: someone asks what is on nearby.

An account either answers every message, or answers only when a rule written by its owner says to. In the second case the agent still reads every message, so it keeps the conversation's context.

## Install

Run this from a checkout of majordomo's `imperson` branch, for example `~/code/majordomo-imperson`:

```bash
cd ~/code/majordomo-imperson/imperson
python3 -m venv venv
venv/bin/pip install -e .. -e .
```

Claude Code must be signed in for the user who runs it (`claude` once, then `/login`), because the Agent SDK runs through it.

## Each account's Chat login

imperson calls majordomo as a library and hands it the path of a majordomo `config.toml`. majordomo looks for `client_secret.json` and `token.json` in the same folder, so each account keeps its own folder.

For example, `~/.config/majordomo/admin/config.toml`:

```toml
[me]
user_id = "users/123456789012345678901"   # the account's own Chat id, so its own messages are not answered
```

Copy the OAuth client file into that folder. Then sign in once as the account, with `HOME` pointed at a scratch folder so your own login is untouched:

```bash
mkdir -p /tmp/login/.config/majordomo && cp client_secret.json /tmp/login/.config/majordomo/
HOME=/tmp/login majordomo login        # choose the account in the browser
mv /tmp/login/.config/majordomo/token.json ~/.config/majordomo/admin/
```

## Configuration: `~/.config/majordomo/imperson.toml`

The file has one table per account:

```toml
[account.admin]
address = "admin@example.com"
majordomo_config = "~/.config/majordomo/admin/config.toml"
tenant = "~/code/business"                 # the business's repository: its people, timezone and skill files
location = "Springfield"            # where "nearby" and "the weather" mean when no place is named
skills = ["reimbursement", "weather", "events"]
send = "off"                                # off | test | live
test_correspondent = "users/…"

[account.owner]
address = "owner@example.com"
majordomo_config = "~/.config/majordomo/config.toml"
tenant = "~/code/business"
location = "Springfield"
skills = ["weather", "events"]
send = "off"
speak_when = """
Speak only when someone asks about the weather or what is on, and say nothing otherwise.
"""
```

- An account without `speak_when` answers every message.
- An account with `speak_when` follows that rule, in plain words. Silence means the session reads the message and sends nothing.
- `send = "off"` prints what would be sent.
- `send = "test"` handles only the test correspondent's messages, and sends everything, payment tasks included, to that person.
- `send = "live"` sends to the real people and spaces.

## The tenant's skill files

These sit in the tenant repository:

- `.imperson/reimbursement/settings.yaml` holds `task_space` (where a payment task goes), `week_starts` (a weekday name), `overdraft_limit`, and `allowances`, a weekly amount per person named as in `.reviewagent/team.yaml`.
- `.imperson/reimbursement/rubric.md` says what counts as a business purchase.
- `.imperson/reimbursement/ledger.tsv` is written by the reimbursement skill, one line per invoice. In live mode each line is committed in the tenant clone. It is not pushed.

People's Chat ids and the timezone are read from `.reviewagent/team.yaml` and `.reviewagent/settings.yaml`, which the tenant repository keeps for reviewagent.

## Running

One run reads every account's direct messages that are new since its last run, hands each message to its conversation's session, and exits:

```bash
venv/bin/python -m imperson                 # or: --account admin, --config <file>
```

To run it every minute, add this line to your own crontab (`crontab -e`). Set `PATH` so that it includes the folder where `claude` is installed:

```
PATH=/home/<you>/.local/bin:/usr/local/bin:/usr/bin:/bin
* * * * * mkdir -p ~/.local/state/majordomo-imperson && flock -n ~/.local/state/majordomo-imperson/run.lock ~/code/majordomo-imperson/imperson/venv/bin/python -m imperson >> ~/.local/state/majordomo-imperson/log 2>&1
```

`flock -n` lets one run finish before the next starts, so a message is never answered twice. To stop the agent, remove the line with `crontab -e`.

The state lives in `~/.local/state/majordomo-imperson/<account>/`. It holds the time of the last run, the messages already handled, each conversation's session id, and the files saved from messages. A run that fails part-way leaves that time where it was, so the next run picks up from the message that failed.

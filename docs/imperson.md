# imperson

imperson answers Google Chat direct messages as an account. It signs in to Chat as that account, through majordomo and the account's own login. Each conversation has its own Claude Agent SDK session, resumed every time the person writes again, so the agent remembers what was said. Its abilities are skills, which Claude picks from what the person writes. imperson carries two:

- **weather**: someone asks about the weather.
- **events**: someone asks what is on nearby.

A business adds its own in its repository's `.imperson/skills/<name>/SKILL.md`, and one of the same name takes the place of imperson's. A business's process, such as how it reimburses staff, is its own, so its skill lives there. The reimbursement skill is one such skill. A skill whose folder in `.imperson/` holds a `policy.yaml` gets three general tools named after it, such as `reimbursement_balance`, `reimbursement_find_match` and `reimbursement_record`. The tools apply that policy and hold none of their own.

An account either answers every message, or answers only when a rule written by its owner says to. In the second case the agent still reads every message, so it keeps the conversation's context.

## Install

imperson is the package `src/imperson/` on majordomo's `imperson` branch, beside `src/majordomo/`. Install the checkout once, for example `~/code/majordomo-imperson`:

```bash
cd ~/code/majordomo-imperson
python3 -m venv venv
venv/bin/pip install -e .
```

This gives the venv both the `majordomo` and the `imperson` commands.

The conversations run through Claude Code, each account in its own configuration folder, so the plugins, hooks and instructions of the person who runs imperson stay out of them. That folder holds no login, so imperson signs in with a long-lived token: run `claude setup-token` once and put the token it prints as `claude_token` at the top of `imperson.toml`. The file then holds a secret, so keep it readable by you alone (`chmod 600`).

## Each account's Chat login

Each account has a folder holding its `token.json` and `client_secret.json`, named by `keys` in `imperson.toml`. imperson asks Google whose token it is, so the account knows its own messages and does not answer them.

To make the token, sign in once as the account. Point `HOME` at a scratch folder, so your own login is untouched. `majordomo login` will not start without a `config.toml` in its folder, so give it an empty one there:

```bash
mkdir -p /tmp/login/.config/majordomo ~/.config/majordomo/admin
cp ~/.config/majordomo/client_secret.json ~/.config/majordomo/admin/
cp ~/.config/majordomo/admin/client_secret.json /tmp/login/.config/majordomo/
touch /tmp/login/.config/majordomo/config.toml
HOME=/tmp/login majordomo login        # choose the account in the browser
mv /tmp/login/.config/majordomo/token.json ~/.config/majordomo/admin/
rm -r /tmp/login
```

## Configuration: `~/.config/majordomo/imperson.toml`

The file holds the Claude token and one table per account:

```toml
claude_token = "…"                          # from `claude setup-token`; keep this file chmod 600

[account.admin]
address = "admin@example.com"
keys = "~/.config/majordomo/admin"          # its token.json and client_secret.json
tenant = "~/code/business"                 # the business's repository: its people, timezone and skill files
location = "Springfield"            # where "nearby" and "the weather" mean when no place is named
skills = ["reimbursement", "weather", "events"]
send = "off"                                # off | test | live
test_correspondent = "users/…"

[account.owner]
address = "owner@example.com"
keys = "~/.config/majordomo"                # the same login your own majordomo uses
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

- `.imperson/skills/reimbursement/SKILL.md` is the process, in prose.
- `.imperson/reimbursement/rubric.md` lists, in its In, Circumstantial and Out sections, the kinds of item this business could buy. The agent places each item in one of these scopes. It does not judge who used the item or what it was for.
- `.imperson/reimbursement/policy.yaml` is what the general tools apply. Its keys:
  - `task_space`, where tasks are posted, and `timezone`.
  - `team`, a file mapping names to Chat ids, which `people` then uses.
  - `period`: `{every: week, starts: Monday}` or `{every: month}`.
  - `ledger`: the ledger's file name in this folder.
  - `claim.fields`, the claim's fields and their kinds (`text`, `date`, `number`), and `claim.amount`, the field summed against a limit.
  - `same_claim`: the fields that must all be equal (`all`), and those of which one must be (`any`), for two claims from different messages to count as the same.
  - `people`: per name or Chat id, a `group` and, for a group with one, a `limit` for the period. `default` covers anyone not listed.
  - `scopes`: for each scope the agent may name, what it means for each group. It is `record`, or `refuse`, or `{record: true, requires: [fields]}`, which records only once the person has given those fields. A person reads those fields; the agent passes them on unjudged.
  - `outcomes`, checked in order:
    - Each has a `name`, the group it is `for`, and an optional `min_left`, which the amount left after the claim must reach.
    - `spent: true` counts the outcome against the limit.
    - `refused: true` marks the outcome recorded when a scope is refused. Such lines are left out of `same_claim`.
    - `task` is the wording posted with the file, filled from the claim's fields and `{person}`, `{scope}`, `{left}` and `{over}`. A `null` task posts nothing.
- The ledger is written by the tools, one line per claim. In live mode each line is committed in the tenant clone. It is not pushed.

## Running

One run reads every account's direct messages that are new since its last run, hands each message to its conversation's session, and exits:

```bash
~/code/majordomo-imperson/venv/bin/imperson          # or: --account admin, --config <file>
```

To run it every minute, add this line to your own crontab (`crontab -e`). Set `PATH` so that it includes the folder where `claude` is installed:

```
PATH=/home/<you>/.local/bin:/usr/local/bin:/usr/bin:/bin
* * * * * mkdir -p ~/.local/state/majordomo-imperson && flock -n ~/.local/state/majordomo-imperson/run.lock ~/code/majordomo-imperson/venv/bin/imperson >> ~/.local/state/majordomo-imperson/log 2>&1
```

`flock -n` lets one run finish before the next starts, so a message is never answered twice. To stop the agent, remove the line with `crontab -e`.

The state lives in `~/.local/state/majordomo-imperson/<account>/`. It holds the time of the last run, the messages already handled, each conversation's session id, and the files saved from messages. A run that fails part-way leaves that time where it was, so the next run picks up from the message that failed.

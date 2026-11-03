# imperson

imperson answers Google Chat direct messages as an account: a Google account, such as a business's admin address, whose Chat it signs in to through majordomo. The example this guide follows is a business whose staff send that account receipts to be paid back. For each receipt the account records a claim, which is a request to be paid back. It then posts a task in the Chat space where the business keeps its tasks: a payment to make when the claim is within the person's spending limit, and a request for approval otherwise. People the business gives authority send the account orders, which are instructions about claims or balances, such as approving a claim or setting what someone has left.

Each conversation has its own Claude Agent SDK session, resumed every time the person writes again, so the agent remembers what was said. What the agent can do comes from skills, which it picks from what the person writes. Reimbursement is one, written by the business. imperson carries `weather` and `events`, in `src/imperson/skills/`, for someone who asks about the weather or what is on nearby, so that the account answers as a correspondent and not as a receipts process alone. An account answers every message, or only those its `speak_when` rule calls for.

imperson calls a business it works for a tenant. A tenant's skills, and the files they work from, live in its GitHub repository, from which imperson fetches them into a cache. A tenant's books are the record imperson keeps in the business's own MySQL database: the files people send, and the claims and orders made of them. A skill's rules, such as who has which allowance, sit in a `policy.yaml` of its own. A skill with a policy gets imperson's general tools, the same for every business and named after the skill, which apply the policy to the books and decide nothing of their own.

Setting imperson up on a host takes an install of imperson, a token that lets it run Claude Code, a Google sign-in for each account and, for each business, a read-only GitHub token for its repository and a MySQL database the host can reach. The sections below take them in turn.

## Install

imperson ships on majordomo's `imperson` branch, as the package `src/imperson/` beside `src/majordomo/`. Install a checkout of majordomo on that branch once, for example `~/code/majordomo-imperson`:

```bash
cd ~/code/majordomo-imperson
git checkout imperson
python3 -m venv venv
venv/bin/pip install -e .
```

This gives the venv both the `majordomo` and the `imperson` commands.

The conversations run through Claude Code, which the Agent SDK installs with itself. Each account has its own Claude Code configuration folder, so the plugins, hooks and instructions of the person who runs imperson stay out of its conversations. That folder holds no login, so imperson signs in with a long-lived token. Run `claude setup-token` once in your own Claude Code, and put the token it prints as `claude_token` in `imperson.toml`. When that token expires or is revoked, every turn fails and each run prints the refusal until a new one is set.

## Each account's Chat login

Each account has a folder holding its `token.json` and `client_secret.json`, named by `keys` in `imperson.toml`. imperson asks Google whose token it is, so the account knows its own messages and does not answer them.

To make the token, sign in once as the account. Point `HOME` at a scratch folder, so your own login is untouched. `majordomo login` will not start without a `config.toml` in its folder, so give it an empty one there:

```bash
mkdir -p ~/.config/majordomo/admin
cp ~/.config/majordomo/client_secret.json ~/.config/majordomo/admin/
scratch=$(mktemp -d) && mkdir -p "$scratch/.config/majordomo"
cp ~/.config/majordomo/admin/client_secret.json "$scratch/.config/majordomo/"
touch "$scratch/.config/majordomo/config.toml"
HOME="$scratch" ~/code/majordomo-imperson/venv/bin/majordomo login    # choose the account in the browser
mv "$scratch/.config/majordomo/token.json" ~/.config/majordomo/admin/
rm -r "$scratch"
```

## Configuration: `~/.config/majordomo/imperson.toml`

The file holds the Claude token, one table per business and one per account:

```toml
claude_token = "…"                          # from `claude setup-token`

[tenant.business]
repo = "example/business"                   # the business's GitHub repository, as owner/name
branch = "main"                             # optional; the default branch found at the first fetch otherwise
github_token = "github_pat_…"               # fine-grained: this repository alone, Contents: read

[tenant.business.db]
MYSQL_HOST = "db.example.com"               # the keys majordomo's own [db] table uses
MYSQL_PORT = 3306                           # optional; 3306 otherwise
MYSQL_USER = "…"
MYSQL_PASSWORD = "…"
MYSQL_DATABASE = "business"

[account.admin]
tenant = "business"
address = "admin@example.com"               # who the agent is told it answers as
keys = "~/.config/majordomo/admin"          # its token.json and client_secret.json
location = "Springfield"                    # where "nearby" and "the weather" mean when no place is named
skills = ["reimbursement", "weather", "events"]
send = "off"                                # off | test | live
test_correspondent = "users/…"              # the person test mode answers, as a Chat id

[account.owner]
tenant = "business"
address = "owner@example.com"
keys = "~/.config/majordomo"                # the same login your own majordomo uses, so this account is you
location = "Springfield"
skills = ["weather", "events"]
send = "off"
speak_when = """
Speak only when someone asks about the weather or what is on, and say nothing otherwise.
"""
```

- The file holds the Claude token, the GitHub token and the database password, so keep the whole file readable by you alone: `chmod 600 ~/.config/majordomo/imperson.toml`.
- Each account names its business with `tenant`. Two accounts of one business name the same tenant, and share its cache and its books.
- The GitHub token is a fine-grained personal access token whose repository access is this repository alone and whose one permission is Contents: read (GitHub adds Metadata: read itself). It can read the repository and nothing else. When it expires or is revoked, GitHub refuses it, and each run prints that refusal and works from the cache until a new token is set here.
- Every tenant needs its `db` table, whatever its accounts' skills, because a run with a message opens the books and writes to them every file the account receives. imperson creates its tables on first use, so the database user needs the right to create tables.
- `address` is the name the agent is told it answers as. The account's Chat token gives its Chat id, by which it knows its own messages, so `address` serves the agent alone.
- `skills` names the skills the account may use, imperson's own or the business's.
- An account without `speak_when` answers every message. An account with it follows that rule, in plain words. Silence means the session reads the message and sends nothing.
- `send` is the account's mode. A run in off or test mode is a rehearsal, whose books are kept apart from the live ones.
- `send = "off"`, the default, prints what would be sent.
- `send = "test"` handles only the messages of `test_correspondent`, the Chat id of the person rehearsing, usually the person who runs imperson. It sends everything, tasks included, to that person.
- `send = "live"` sends to the real people and spaces.

## The business's files

imperson reads a business's files from its GitHub repository through the API, with the tenant's token. It fetches `business-overview.md` at the repository's root, the whole `.imperson/` tree, and the team file each policy names under `team`. Nothing else of the repository is fetched. The files are kept in `~/.cache/majordomo-imperson/tenant/<name>/` at their repository paths, and `tenant.json` beside them records the branch, the commit and each file's git blob hash.

The cache is brought up to the branch once per tenant, in a run that has a message to answer. When the branch has not moved, that costs one conditional request. When it has moved, imperson compares each wanted file's blob hash with the cached one, fetches only the files that differ, and removes any file gone from the repository. A commit that changes none of these files costs the tree listings and fetches no file.

When GitHub cannot be reached or refuses the token, the run works from the cache and says so. With no cache yet, the account stops before reading any message, and the next run tries again.

A conversation's file tools reach the cache and the account's `work/` folder (see The state), and nothing else. The conversation may search and fetch the web, and it can neither run commands nor write files. Its system prompt names the cache's folder. A conversation resumed after a file in the business's cache, or one of imperson's own skills, changed is told which files changed, before its message.

## The books

A business's books are tables in its own database. A live account writes tables whose names begin `imperson_`. Rehearsal books use the prefix `imperson_test_`.

- `files` holds each file's content once, found by its SHA-256, with its media type and size.
- `uploads` holds every file received, one row per arrival, keyed by Chat's attachment name. It records the account, the message, the sender, the file's name and type, the time received, and the file it holds. A file that could not be fetched, or that is larger than the database server's `max_allowed_packet`, is an upload with an error and no file; a later arrival of the same attachment with its content fills the file in.
- `claims` holds one claim per invoice per message: the skill, the account, the person, the message, the time, the scope, the outcome (both named by the policy, below), the summed amount and the claim's fields. `claim_uploads` links each claim to the uploads it rests on. A claim the policy counts as the same as an earlier one is marked a duplicate of that claim.
- `orders` holds each order: who gave it, in which message and when, its kind, the person or the claim it concerns, the amount a `set_left` set and how far it moved what was left, and the words it was given in.
- `schema` holds the books' version, and a run against books at another version stops.

A hosted database server may take connections only from addresses it lists (cPanel's Remote MySQL list, for one); add the host imperson runs on. A file is stored in one statement, so the server's `max_allowed_packet` limits the size of a file the books can hold.

A run writes a message's files to the books before the conversation sees the message. A database that cannot be reached stops the account before any conversation starts, and a message whose files cannot be written waits for the next run.

The conversation is told of each file in one line. A file saved from the message comes with its path, its upload number (the `uploads` row's id) and its content type. When the books already hold the same bytes from an earlier message of this conversation, the line names the upload they first came with, when, and in which message. When the books could not keep the file, because it is too large for the database, the file still sits at its path, and the line adds that the books did not keep it and why. A file that could not be fetched at all has no path, and its line gives its name, its upload number and the error. The upload number is what a claim names its files by.

## The business's skills and the policy

A business writes these in its repository:

- `business-overview.md` describes the business, for a skill that tells the agent to read it.
- `.imperson/skills/<skill>/SKILL.md` is a skill: its process, in prose, for the agent. A skill with the same name as one of imperson's own replaces it. An account uses it when its `skills` names it.
- `.imperson/<skill>/` holds the files the skill works from. The reimbursement skill keeps `rubric.md` there, whose In, Circumstantial and Out sections describe the kinds of item the business could buy. The agent places each item in one of these scopes. It does not judge who used the item or what it was for. A circumstance the person gives is passed on unjudged, for a person to read.
- `.imperson/<skill>/policy.yaml` is what the general tools apply. A policy for reimbursement reads, for example:

```yaml
task_space: spaces/…                       # where tasks are posted
timezone: Australia/Brisbane
team: .team/people.yaml                    # its `people` maps each name to a Chat id: {Ann: users/…, Bo: users/…}
period: {every: week, starts: Monday}      # or {every: month}
claim:
  fields: {vendor: text, invoice_date: date, amount: number, circumstance: text}
  amount: amount                           # the field summed against a limit
same_claim: {all: [invoice_date], any: [vendor, amount]}
people:
  Ann: {group: allowance, limit: 200}
  users/…: {name: Cy Example, group: allowance, limit: 100, carry_over_from: 2026-01-05}
  default: {group: by-approval}
authority: [Ann]
orders:
  approve: "Claim #{claim} of {person} approved by {given_by}: {words}"
  refuse: "Do not pay claim #{claim} of {person}. Refused by {given_by}: {words}"
scopes:
  in:             {allowance: record, by-approval: record}
  circumstantial: {allowance: refuse, by-approval: {record: true, requires: [circumstance]}}
  out:            {allowance: refuse, by-approval: refuse}
outcomes:
  - {name: within, for: allowance, min_left: 0, spent: true, task: "Pay {person} {amount}, {vendor}. {left} left."}
  - {name: over-limit, for: allowance, task: "Decide on {person}'s {amount}, {vendor}: over the limit by {over}."}
  - {name: for-approval, for: by-approval, task: "Approve {person}'s {amount}, {vendor} ({scope}): {circumstance}"}
  - {name: approved, approved: true, spent: true, task: "Pay {person} {amount}, {vendor}. Approved by {given_by}."}
  - {name: duplicate, duplicate: true, task: null}
  - {name: refused, refused: true, task: null}
```

Its keys:

- `task_space` is the space tasks are posted to, one the account is a member of. `timezone` sets where a period begins and the times the tools show.
- `team` is a YAML file in the repository, whose `people` maps each name to a Chat id. Wherever the policy names a person, a name from it or a Chat id will do.
- `period` is `{every: week, starts: <day>}` or `{every: month}`. A limit applies to each period.
- `claim.fields` names the claim's fields and their kinds, `text`, `date` or `number`. `claim.amount` names the field summed against a limit.
- `same_claim` decides when a claim repeats one from another message, whoever sent it: every field in `all` is equal, and at least one in `any`. Text is compared without case or surrounding spaces, and numbers as numbers. Claims whose outcome is marked `refused` or `duplicate` are not matched.
- `people` gives each person a `group` and, for a group with one, a `limit` per period. `default`, which every policy needs, covers anyone not listed. A person keyed by a Chat id who is not in the team file takes a `name`, by which orders and reports may name them. Groups are names the policy makes up: in the example, `allowance` has a limit, and `by-approval` has none, so each of its claims waits on a person's say. `carry_over_from` carries unused limit over from the period holding that date: the limit counts once for each period since, and the balance counts every claim since.
- `authority` lists the people who may give orders, see another person's balance and read everyone's claims and orders: each a name from the team file, the `name` of a `people` entry, or a Chat id.
- `orders.approve` and `orders.refuse` are the wordings an approve or refuse order posts on a claim whose task has already been posted, as `reimbursement_order`, below, describes. Without the wording, such an order is refused.
- `scopes` gives, for each scope the agent may name, what it means for every group. It is `record`, or `refuse`, or `{record: true, requires: [fields]}`, which records only once the person has given those fields.
- `outcomes` are checked in order, and the first that fits a recorded claim applies. An outcome fits a claim from its group (`for`; any group when absent) when what is left after the claim reaches its `min_left`, or always when it has none; an outcome with `min_left` fits only a group with a limit. An outcome without `min_left` after one with it catches the claims the first did not, as `over-limit` follows `within` in the example. `spent: true` counts the claim against the limit.
- An outcome with a flag is chosen by the flag alone. `refused: true` is recorded when the scope is refused for the group, and given by a refuse order. `duplicate: true` is recorded when `same_claim` finds an earlier claim. `approved: true` is given by an approve order.
- `task` is the wording posted when the outcome is reached, with the claim's files. A `null` task posts nothing.

A wording may name each of the claim's fields, `{person}`, `{scope}`, `{claim}` (its number), `{left}` (what the person has left, less the claim when its outcome is spent), `{over}` (how far the claim takes what is left below zero) and `{duplicate_of}` (the number of the claim it repeats). The summed field, `{left}` and `{over}` are written as money, such as `1,234.50`. A wording an order posts may also name `{given_by}`, `{words}` (the order as given), `{outcome}` (the claim's new outcome) and `{earlier}` (its outcome before). Wordings are Python format strings, so a literal brace is written twice. A wording that names anything else stops the tool before anything is written, and the tool says what it may name.

## The tools a skill with a policy gets

A skill named in an account's `skills`, whose folder holds a `policy.yaml`, gets these tools, named after it, here for a skill called `reimbursement`. The writer is the person whose message is being answered, the sender and not the business that wrote the policy. The agent passes a claim's fields, its scope and the uploads it rests on. The tools take every figure, and who has authority, from the policy and the books.

- `reimbursement_balance` tells the writer's group, what each scope means for it and, for a group with a limit, the limit, what is used and what is left. A writer with authority may pass `person`, a name or a Chat id.
- `reimbursement_find_match` lists the earlier claims, from other messages, that `same_claim` counts as the same.
- `reimbursement_record` records one claim, with its fields, its scope and `uploads`, the upload numbers of the files it rests on. Those files come from this message or an earlier one in the same conversation. The policy decides whether the claim is recorded or refused, or which field it still needs, chooses the outcome, and posts the outcome's task with the claim's files, written out of the books when the account's `work/` folder does not hold them. The same claim recorded again at the same message writes nothing. A claim that repeats an earlier one is recorded under the outcome marked `duplicate`, and the tool's answer names the earlier claim, for the reply to tell the person.
- `reimbursement_order` carries an order from a writer in `authority`, passed with their words; the code refuses an order from any other writer. `set_left` makes what a person has left the amount ordered, or their full limit when the amount is left out. `approve` and `refuse` give a claim the outcome marked `approved` or `refused`, and an approval of a duplicate is refused, naming the claim it repeats. When the claim's own task has already been posted, the order posts the policy's `orders` wording, without files, in place of the new outcome's task, so a payment task is posted once.
- `reimbursement_report` lists the most recent claims and orders, newest first, fifty at most of each, so that a reply stays readable. A writer with authority sees everyone's, and may narrow them with `person`; any other writer sees their own. `since`, an ISO date, narrows either.

The tools work on the business's books, so the business's accounts in one mode see the same claims. A policy the tools cannot read leaves the conversation running without them, and the run prints why.

## Running

One run reads every account's direct messages that are new since its last run, hands each message to its conversation's session, and exits:

```bash
~/code/majordomo-imperson/venv/bin/imperson          # or: --account admin, --config <file>
```

An account's first run reads back one day. Each run prints what it sends, one line per message, or in off mode what it would send.

To run it every minute, add this line to your own crontab (`crontab -e`):

```
* * * * * mkdir -p ~/.local/state/majordomo-imperson && flock -n ~/.local/state/majordomo-imperson/run.lock ~/code/majordomo-imperson/venv/bin/imperson >> ~/.local/state/majordomo-imperson/log 2>&1
```

`flock -n` skips a run while the one before is still going, so two runs do not handle the same message. The conversations run the Claude Code that the Agent SDK carries, so the crontab needs no `PATH` for it. To stop the agent, remove the line with `crontab -e`.

## The state

Each account keeps its state in `~/.local/state/majordomo-imperson/<account>/`:

- `state.json` holds the mark, which is the time of the last complete run; the messages already handled in each conversation; each conversation's session id; and a hash of each file in the business's cache and in imperson's skills, as each conversation last read it.
- `work/` holds each message's files, in a folder named after the message, and, under `work/uploads/<upload>/`, the files written out of the books for a task.
- `claude/` is the account's Claude Code configuration folder, which keeps the sessions themselves.
- `workspace/` is the conversations' working folder, holding links to imperson's skills and the business's.
- `cache/` is majordomo's cache of people's names, for this login.

A run that fails part-way leaves the mark where it was. The next run reads from the mark again, passes over the messages already handled, and tries the failed message again. A reply that the failed turn had already sent is sent again.

The mark, the handled messages and the sessions belong to the account in every mode. A message read in a rehearsal is not read again when the account goes live. A message from anyone but the test correspondent that arrives in test mode is passed over for good. A conversation carried from a rehearsal into live mode remembers claims the live books do not hold, so before an account goes live, set `sessions` to `{}` in its `state.json` and leave the mark and the handled messages: each conversation then starts afresh, and the rehearsal's messages are not answered again. Removing the whole state folder instead reads back one day, so the last day's messages, the test correspondent's among them, would be answered live.

## Running the tests

The suite runs with `uv run --extra mcp --extra dev python -m pytest`. imperson's tests of the books and of the run need a MariaDB. `tests/conftest.py` starts `mariadb:10.5` in Docker the first time a test asks for it, and removes it at the end. Where `IMPERSON_TEST_MYSQL` names a server, as `mysql://user:password@host:port/database`, the suite uses that server and starts no container. Without Docker or that variable, those tests fail with a message saying so. The suite's tables carry their own prefix, `imperson_pytest_`, which keeps them apart from live and rehearsal books on the same server.

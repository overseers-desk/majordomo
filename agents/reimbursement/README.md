# Reimbursement agent

Employees send a photo or PDF of an invoice to the business's Google Chat account in a direct message. The agent answers in that conversation as that account. It decides whether the purchase is ordinary business spending under the rubric of the tenant, the business it runs for, whose repository holds its rules and its ledger, checks it against what was already reimbursed, and works out what is left of the employee's weekly allowance. It then asks a person in the tenant's accounts space to pay. Google Chat marks the agent's messages as sent by an app.

## Install

The agent runs from this directory with majordomo and PyYAML. Claude Code (`claude`) must be on the PATH and signed in, because the judgement runs `claude -p` with the skill in `.claude/skills/reimbursement`.

```bash
python3 -m venv venv
venv/bin/pip install -e ../.. -e .
```

majordomo signs in as the account employees write to: `majordomo login` with that account, and its `config.toml` naming `[me] user_id`, so the agent can tell its own messages.

## Configuration

A TOML file passed with `--config`:

```toml
majordomo_config = "~/.config/majordomo/config.toml"  # the account the agent reads and answers as
tenant = "/path/to/tenant-repository"                   # a clone the agent commits its ledger in
state_dir = "~/.local/state/reimbursement"              # the mark, downloaded invoices, a test ledger
send = "off"                                            # off | test | live
test_correspondent = "users/123"                        # needed for send = "test"
```

- `off` reads and judges, prints each reply and task, and sends nothing. It records into `<state_dir>/ledger-test.tsv`.
- `test` handles only the test correspondent's invoices and sends both the reply and the task to that person's direct message. It also records into the test ledger.
- `live` handles the invoices of every person in the tenant's `.reviewagent/team.yaml`, sends the task to the accounts space, and commits each line of `.reimbursement/ledger.tsv` in the tenant clone. It does not push.

## The tenant's files

These live in `.reimbursement/` in the tenant repository:

- `settings.yaml`, with `task_space` (the space a payment task goes to), `week_starts` (a weekday name), `overdraft_limit`, and `allowances`, a weekly amount per person named as in `.reviewagent/team.yaml`.
- `rubric.md`, what counts as ordinary business spending, in the business's words. The skill judges against this file alone.
- `ledger.tsv`, written by the agent: one line per invoice message, with when it was handled, the employee, the vendor, the invoice date, the amount, the Chat message and the outcome.

People's Chat ids are read from `.reviewagent/team.yaml`, and the timezone from `.reviewagent/settings.yaml`. The tenant repository keeps those two files for reviewagent, the business's review agent, and this agent reads them where they are.

## What one run does

```bash
venv/bin/python -m reimburse --config reimbursement.toml
```

A run reads the direct messages active since the last run. It takes each new message carrying an image or a PDF from a person the team names. A file that is not an invoice or receipt, such as a screenshot, gets no reply and is recorded as `not-an-invoice`. Each invoice comes out as one of these:

- **rejected**: it does not qualify. The reply gives the rubric's reason and a hint.
- **duplicate**: an invoice with the same date, and the same vendor or the same amount, was already reimbursed. The reply names it.
- **within**: it fits the week's allowance. The reply gives what is left, and a task goes to the accounts space with the invoice attached.
- **overdrawn**: it overdraws the week by no more than `overdraft_limit`. The reply says so, and a task goes to the accounts space.
- **over-limit**: it goes beyond the overdraft. Nothing is counted as reimbursed. The task asks the accounts space to decide whether to pay.

An invoice already in either ledger is never handled again. A run where a judgement or a download fails keeps its starting point, so the next run reads those messages again.

Each run is one pass. Running it on a schedule needs a timer on the host.

## Limits

- Only messages carrying a file are read. An employee's text reply to a rejection reaches the person who reads that account, not the agent.
- The ledger is committed in the host's clone and pushed by a person.
- The agent does not write to the accounting system (Xero). A person pays from the task by hand.

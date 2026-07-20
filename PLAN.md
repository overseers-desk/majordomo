# majordomo

A plan for turning this command-line reporter into a packaged accessor for Google Chat.

## What it is

majordomo is a command-line tool that reads Google Chat, reports on it, and sends messages into it. The command line is the primary interface; an MCP server is a secondary interface for AI agents. Both are thin front doors over one shared core, and the core is written so the same design can later reach sources other than Google.

The name is the household steward who runs a principal's affairs and decides what reaches them. It was chosen for that gatekeeping sense, which matches the access filter described below, and because it is unclaimed on PyPI and apt as of 2026-05-22. Names beginning with `google-` are avoided because that namespace reads as official Google software, and `gchat` / `gchat-cli` are already taken and imply a generic chat client rather than this reporter.

## The problem it solves

When a Google Chat user creates a task through "Create a task for @Person (via Tasks)", that task cannot be retrieved through the Google Tasks API; the API returns nothing for tasks created this way. The only durable signal is the chat message "Created a task for @Person (via Tasks)". The existing limitations note in this repository documents the investigation behind this.

majordomo therefore reconstructs task activity by reading chat messages and parsing those task-creation patterns, then reports who holds which tasks across spaces over a date range. Reconstruction from messages, not the Tasks API, is the core value, and no off-the-shelf tool does it.

A second concern is privacy. When an AI agent uses the tool, some spaces should remain invisible to it. majordomo applies an access filter (the "sieve") that drops blocked spaces before any caller sees them. The sieve lives in the core, so every interface inherits it and none can bypass the gate.

## Architecture

- One importable core holds the Google Chat logic, the configuration, and the sieve.
- Thin front doors call the core and add no logic of their own: the command-line interface (primary) and the MCP server (secondary). An automation script that drives the command line is just another caller of the same core.
- Because the sieve and the credentials live in the core, behaviour stays consistent across every front door, and a single change reaches all of them.

## Configuration

Two files under the tool's config directory (for example `~/.config/majordomo/`):

- A human-authored config file (TOML). It holds what a person edits by hand: which spaces and assignees to include or ignore, output preferences, the long-lived OAuth refresh token, and the sieve's allow and block lists.
- A separate machine-managed token file (JSON). It holds the short-lived access token and its expiry, which the program rewrites on every refresh. Keeping it apart from the hand-edited config means a refresh never overwrites a person's edits.

Credentials are keyed by identity, so several Google accounts can be addressed by name rather than by swapping files.

## Relationship to gchat-cli

`gchat-cli` (the project `chadsaun/gchat`, MIT) already implements the accessor layer majordomo needs: OAuth with multi-account support, TOML configuration, listing spaces, reading and searching messages, sending, and JSON output. It is a single-author build of about twenty hours from January 2026 with no activity or users since, so it is best treated as MIT source to fork and own rather than as a maintained dependency. It does not do task reconstruction, assignee reporting, the sieve, an MCP interface, or automation, which is exactly the part that is majordomo's own.

Decision pending one check: whether to adopt it as the accessor base depends on confirming that its read and search return the complete message history over a date window, since task reconstruction needs every message in range and the simple read path appears to cap at recent messages.

## Orchestration stays outside

Automatic processing of tasks and messages is a goal, but the work is a long tail of many distinct processes that each occur a few times a week and need a judgement per message (for example, recognising a trusted sender asking for a date of birth, or checking whether a document has reached a cloud drive), rather than a few processes running at high volume. That shape favours an agent driving the command-line tool over a fixed workflow graph.

Whatever schedules or triggers the tool (a cron entry, a systemd timer, or a workflow engine) drives the command line from outside. The data-pipeline orchestrators Prefect, Dagster, and Airflow, and the automation platform n8n, were surveyed; none offers a Google Chat reading accessor, and none should host majordomo's logic. Orchestration is a deployment choice to make when a concrete recurring need appears, not a part of the tool.

## Scope and next steps

Decided:

- Name majordomo; a command-line-first accessor with an MCP interface, written to extend beyond Google.
- A core holding the Google Chat logic, configuration, and sieve, with thin front doors.
- The configuration split above, with identity-keyed credentials.
- Task reconstruction from chat messages as the core capability.
- Send through both front doors (`send --space|--thread TEXT`), the sieve refusing blocked targets.
- Orchestration kept external.

Open:

- Whether to fork and vendor gchat-cli as the accessor base, pending the pagination check.
- The concrete shape of the automatic processing.
- Packaging as a Debian package (intended).

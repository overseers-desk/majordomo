# majordomo: design

A shorter forward plan, with the decided-and-open scope as a checklist, lives alongside in `PLAN.md`.

## What majordomo is

majordomo is a command-line tool that reads Google Chat and reports on it. The command line is the primary interface; an MCP server is a secondary interface for AI agents. Both are thin front doors over one shared core, and the core is written so the same design can later reach sources other than Google.

Its name is the household steward who runs a principal's affairs and decides what reaches them. The choice points at the access filter described below, and the word is unclaimed on PyPI and apt as of 2026-05-22. Names beginning with `google-` are avoided because that namespace reads as official Google software, and `gchat` and `gchat-cli` are already taken and imply a generic chat client rather than this reporter.

## The problem

### Task reconstruction

When a Google Chat user creates a task through "Create a task for @Person (via Tasks)", that task cannot be retrieved through the Google Tasks API; the API returns nothing for tasks created this way. The only durable signal is the chat message "Created a task for @Person (via Tasks)". `GOOGLE_CHAT_TASKS_LIMITATIONS.md` documents the investigation behind that finding.

Task activity therefore has to be reconstructed from chat messages by parsing those task-creation patterns, then reported by who holds which tasks across spaces over a date range. The BI platform already does this reconstruction server-side (a `coord_tasks` table over a `googlechat` mirror); majordomo reports over it when that backend is present and runs its own message decoder when it is not. Which source serves which path is in `DATA-MODEL.md`.

### Privacy gating

When an AI agent uses the tool, some spaces must remain invisible to it. majordomo applies an access filter, called the sieve, that drops blocked spaces before any caller sees them. The sieve sits in the core, so every interface inherits it and none can bypass the gate.

The sieve's two block lists (`block_spaces` and `block_assignees`) live in the human-authored TOML and are applied in the core, so every front door inherits them.

## Capabilities

All capabilities are reachable through both front doors:

- List spaces the account belongs to.
- Read messages and spaces over a date range — from the BI platform's cache as the fast path, or a live Chat read paginated to completeness when that backend is absent.
- Report task activity (creation, assignment, and other lifecycle signals): from the BI platform's `coord_tasks` reconstruction when present, from majordomo's own message decoder when standalone.
- Report tasks by assignee, by space, and by date range.
- Resolve user identifiers to display names via the People API, so reports name people rather than opaque IDs.
- Apply the sieve, dropping blocked spaces from every output path.
- Address several Google accounts by identity, without swapping configuration files.
- Emit JSON for scripting alongside human-readable output.

## Architecture

### Core

One importable Python package holds the entire business behaviour: a reader for the BI platform's cache (the fast path) and a Google Chat client wrapping the official API (the standalone path), the configuration loader, the sieve, the task decoder, reporting, multi-account credential management, and the People-API name resolver. The cache reader is the primary source and the live client its fallback; the backend accelerates, it does not gate. The core has no command-line parsing and no MCP protocol code; it exposes functions and types that any caller can use.

### Front doors

Two front doors call the core and add no logic of their own:

- The command-line interface is the primary one. It is what a person types at a terminal, what a cron entry or systemd timer triggers, and what an automation script drives.
- The MCP server is the secondary one. It exposes the same operations as MCP tools so AI agents can call them through that protocol.

Because the sieve and the credentials live in the core, behaviour stays consistent across every front door, a single change reaches all of them, and no front door can bypass the access gate by accident.

## Configuration

### The two files

Two files under the tool's config directory (for example `~/.config/majordomo/`):

- A human-authored TOML file. It holds what a person edits by hand: which spaces and assignees to include or ignore, output preferences, the long-lived OAuth refresh token, and the sieve's allow and block lists.
- A separate JSON file for the access token and its expiry, which the program rewrites on every refresh.

The split exists because the volatile and the stable should not share a file. If the access token sat in the TOML, an automatic refresh would have to rewrite a file the person edits by hand; comments and formatting would not survive, and concurrent edits could race. Keeping the rewritten file apart makes the program and the person each own their own surface.

This replaces the present `config/client_secret.json` and `config/token.json` pair under the repository tree; the OAuth client secret stays in its own file as Google issues it, and the per-account refresh token moves into the TOML keyed by identity.

### Multi-account by identity

Credentials are keyed by identity, so several Google accounts can be addressed by name from any front door rather than by swapping files. The shape is a `[identity.NAME]` table mapping a name to its credentials, which scales to as many accounts as a person uses.

## The sieve

The sieve is an allow-list and block-list of spaces. Its purpose is to keep certain conversations out of the agent's view: a user who chats with the tool through MCP should not have private spaces returned by `list_spaces` or scanned by `read_messages`. The lists live in the human-authored TOML, are loaded at the start of every call, and are applied inside the core before any space identifier or message reaches the caller.

Placing it in the core, not in a wrapper, means any front door (and any future front door) inherits it for free; a future automation that talks to the core directly cannot work around a wrapper-only gate.

## Naming and packaging convention

The distribution name on PyPI and the Debian package name follow the lowercase-hyphen convention (`majordomo`). The import package uses underscores because hyphens are not valid in Python identifiers; for a single word the two are the same.

The `google-` prefix is avoided as a brand choice. In the apt ecosystem `google-*` is in practice Google's own namespace, and the community convention for third-party tools targeting a Google product is a brand-neutral or `g`-prefixed name with the product cited in the description (nominative fair use).

## Relationship to other tools

### crude and mailroom

majordomo is an information-flow reader and reporter, not an object-edit tool, so it lives in neither of the user's neighbouring accessors. crude is rejected as a home: its CRUD/object grammar does not fit a read-mostly message flow whose tasks are reconstructed, not stored. mailroom is the architectural template, not the host: its sieve and provenance-tagged cache-with-live-fallback carry across, but its email-specific maildir+mu cache does not. The cache is mandatory, since Google throttles live reads (a hundred-plus-item scan can take minutes, and users expect mailroom speed), but majordomo does not build it: it reads the BI platform's existing server-side `googlechat` mirror and `coord_tasks` reconstruction (direct DB first, an API later), keeping its own live read and task decoder to run without that backend. How it serves callers, a query CLI or a daemon over REST in the Evolution API style, is open. `DATA-MODEL.md` holds the full reasoning.

### gchat-cli

`gchat-cli` (the project `chadsaun/gchat`, MIT) already implements the accessor layer majordomo needs: OAuth with multi-account support, TOML configuration, listing spaces, reading and searching messages, sending, and JSON output. It is a single-author build of about twenty hours from January 2026 with no activity or users since, so it is best treated as MIT source to fork and own rather than as a maintained dependency.

gchat-cli matters only for majordomo's standalone live read, the fallback when the BI cache is absent, not the fast path, which reads the existing mirror. Forking its accessor for that fallback would save rebuilding OAuth, multi-account, and paginated message reads; what it does not do is majordomo's own work either way: assignee reporting, the sieve, the MCP interface, identity-keyed reporting, and automation.

The pending check before adopting gchat-cli for that fallback is whether its read and search return the complete message history over a date window, since a standalone reconstruction needs every message in range and the simple read path appears to cap at recent messages.

## Orchestration

Whatever schedules or triggers majordomo lives outside it. The cheapest option is a cron entry or a systemd timer. A workflow engine (Prefect, Dagster, Airflow, or n8n) can drive the command line through an Execute Command node or a `BashOperator`, and through MCP via a client node where the engine has one. The choice of driver is a deployment decision to make when a concrete recurring need appears.

The intended automatic processing has a particular shape that informs this choice: a long tail of many distinct processes, each occurring a few times a week and needing a judgement per message (recognising a trusted sender asking for a date of birth, checking whether a document has reached a cloud drive, classifying an inbound request and routing it). The profile is not a few processes at high volume. That shape favours an agent driving the command line over a fixed workflow graph, because a static graph rewards a small number of well-defined flows that run often enough to amortise the cost of authoring them, while many low-frequency processes are cheaper to express as tool calls by a model that already understands the message.

None of the workflow orchestrators surveyed ships a reading accessor for Google Chat that would substitute for majordomo's core; their Google Chat integrations, where present, are send-only webhook alerters. So orchestration stays a caller of majordomo, not a host.

## Distribution

majordomo is packaged for `pip` (PyPI) and for Debian (a `.deb`), following the lowercase-hyphen distribution name convention.

## Decisions and open questions

Decided:

- Name `majordomo`; a command-line-first accessor with an MCP interface, written to extend beyond Google.
- A core that holds the Google Chat logic, the configuration, and the sieve, with thin front doors.
- The two-file configuration split: TOML for what a person edits, JSON for what the program rewrites.
- Credentials keyed by identity.
- Task activity reported from the BI platform's reconstruction (`coord_tasks`) when present, from majordomo's own message decoder when standalone; the decoder is retained so majordomo runs without that backend.
- Sieve enforced in the core, never only in a front door.
- Orchestration kept external.
- The accessor's data model is an information flow (crude's object-edit model is rejected). The fast path reads the BI platform's existing cache and reconstructed tasks, mandatory cache-first because Google throttles live reads, while majordomo keeps its own live read and decoder to run standalone. Delivery (query CLI versus REST daemon) is open (`DATA-MODEL.md`).

Open:

- Whether to fork and vendor gchat-cli as the accessor base, pending the pagination check.
- The concrete shape of the automatic processing (which message classes get which actions, where the agent loop lives).
- The packaging skeleton (Python project layout, the `pyproject.toml` shape, the Debian `debian/` files).

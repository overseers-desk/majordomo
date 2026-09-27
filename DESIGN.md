# majordomo: design

A shorter forward plan, with the decided-and-open scope as a checklist, lives alongside in `PLAN.md`.

## What majordomo is

majordomo is a command-line tool that reads Google Chat, reports on it, and sends messages into it. The command line is the primary interface; an MCP server is a secondary interface for AI agents. Both are thin front doors over one shared core, and the core is written so the same design can later reach sources other than Google.

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

- List spaces the account belongs to, whether each belongs to a Google Workspace domain or a consumer/personal account, and (on request) who owns it.
- Read messages and spaces over a date range — from the BI platform's cache as the fast path, or a direct Chat read paginated to completeness when that backend is absent.
- Report task activity (creation, assignment, and other lifecycle signals): from the BI platform's `coord_tasks` reconstruction when present, from majordomo's own message decoder when standalone.
- Report tasks by assignee, by space, and by date range.
- Send a message into a space, or a reply into a thread, as the authenticated account, with optional file attachments.
- List the files posted in a space, a thread, or on one message, and save them to disk.
- Name people and spaces: take a person as an id, an email or a name, a space as an id or a display name, and show every person by name through the People API (see "Naming people and spaces").
- Apply the sieve, dropping blocked spaces from every output path.
- Address several Google accounts by identity, without swapping configuration files.
- Emit JSON for scripting alongside human-readable output.

## Architecture

### Core

One importable Python package holds the entire business behaviour: a reader for the BI platform's cache (the fast path) and a Google Chat client wrapping the official API (the standalone path), the configuration loader, the sieve, the task decoder, reporting, multi-account credential management, and the person and space resolver with its cache files (roster.py). The cache reader is the primary source and the direct-API client its fallback; the backend accelerates, it does not gate. Three read modes follow: `cache` (the mirror), `nocache` (the direct API, no cache), and `live` (up-to-dateness). The `live` mode serves the cache and tops it up from the API for records newer than each space's cache watermark, polling only recently-active spaces so an unscoped freshness read stays within the read quota. "live" names the need (currency), not a mechanism; the cache bypass is `nocache`. The core has no command-line parsing and no MCP protocol code; it exposes functions and types that any caller can use.

### Front doors

Two front doors call the core and add no logic of their own:

- The command-line interface is the primary one. It is what a person types at a terminal, what a cron entry or systemd timer triggers, and what an automation script drives.
- The MCP server is the secondary one. It exposes the same operations as MCP tools so AI agents can call them through that protocol.

Because the sieve and the credentials live in the core, behaviour stays consistent across every front door, a single change reaches all of them, and no front door can bypass the access gate by accident. This is the first invariant in [`INVARIANTS.md`](INVARIANTS.md).

## Configuration

### The two files

Two files under the tool's config directory (for example `~/.config/majordomo/`):

- A human-authored TOML file. It holds what a person edits by hand: which spaces and assignees to include or ignore, output preferences, the long-lived OAuth refresh token, and the sieve's allow and block lists.
- A separate JSON file for the access token and its expiry, which the program rewrites on every refresh.

The split exists because the volatile and the stable should not share a file. If the access token sat in the TOML, an automatic refresh would have to rewrite a file the person edits by hand; comments and formatting would not survive, and concurrent edits could race. Keeping the rewritten file apart makes the program and the person each own their own surface.

This replaces the present `config/client_secret.json` and `config/token.json` pair under the repository tree; the OAuth client secret stays in its own file as Google issues it, and the per-account refresh token moves into the TOML keyed by identity.

Authentication is per-account OAuth: each account signs in through Google's browser consent flow and majordomo acts as that user. It uses neither a service account nor domain-wide delegation. A service account is a non-human identity that cannot read a given user's Chat on its own, and delegation reaches only accounts inside a Google Workspace domain that an administrator has enabled, excluding consumer Gmail. Per-account consent works for any account that can grant it, at the cost of one login per user.

### The name cache

A third file kind sits apart from both: the people and spaces majordomo has fetched, under the XDG cache directory (`$XDG_CACHE_HOME/majordomo/`, `~/.cache/majordomo/` when unset, on Linux and macOS alike). Two files, `people.json` and `spaces.json`, each a single JSON object keyed by resource name (`people/<id>`, `spaces/<id>`) whose value is `{"fetched_at": <ISO time>, "person"|"space": <the API object as returned>}`: the People API's `Person` and the Chat API's `Space`, a direct-message space returned by `findDirectMessage` included. Everything in it is refetchable, so it is a cache, and storing the API's own form avoids a private schema. A file that changed is rewritten whole through a temporary file and a rename, so a reader never sees half of one. A person fetched more than thirty days ago is fetched again when next shown, so a rename reaches the reports. Under `WORLD_AS_OF` the cache is read and never written.

### Multi-account by identity

Credentials are keyed by identity, so several Google accounts can be addressed by name from any front door rather than by swapping files. The shape is a `[identity.NAME]` table mapping a name to its credentials, which scales to as many accounts as a person uses.

## Naming people and spaces

A message names its sender by `users/<id>` and nothing else, in the mirror and from the Chat API alike; the user table carries no display name or email, and membership is not mirrored. The People API names the person: a Chat `users/<id>` is the People API's `people/<id>`, read with `people.getBatchGet` ([reference](https://developers.google.com/people/api/rest/v1/people/getBatchGet)), up to 200 ids per call, asking `personFields=names,emailAddresses,metadata`.

The People API consults only the sources a request names, and unset it reads the profile and saved contacts alone. majordomo names three ([`ReadSourceType`](https://developers.google.com/people/api/rest/v1/ReadSourceType)): `READ_SOURCE_TYPE_PROFILE` (which returns the Google profile, the Workspace domain profile and the account), `READ_SOURCE_TYPE_CONTACT` (the signed-in account's saved contacts), and `READ_SOURCE_TYPE_OTHER_CONTACT` (its "other contacts", the people it has interacted with but not saved). The last is the only source that names some colleagues, and a consumer account's public profile often carries no name at all. When several sources name a person, the profile or domain-profile name wins over a contact label, a contact label being the signed-in user's own naming of someone. The same answer carries email addresses, which is how an email resolves to an id without a Chat call.

The one resolver lives in the core (roster.py) and every report that shows a person goes through it: task assignees on both paths, message and attachment senders, space owners under `spaces --owner`, and `people`. It reads the cache file first, asks the People API in batches for ids not there, and writes the answers back. `block_assignees` is applied both to the name a row came with and to the resolved name, so a person blocked by either stays out. An id the API cannot name, or a run whose token cannot reach it, shows the bare `users/<id>`; the report still answers.

Every command that takes a person (`--person`, `--assignee`, `--to`) accepts `users/<id>`, an email, or a name; every command that takes a space accepts `spaces/<id>` or its display name. Resolution runs inside the core, in the reader or in the send and attachments functions, so both front doors pass the string through and neither can bypass the sieve on the resolved space. A name is matched case-insensitively, whole name first and then as a substring, and must match one person: several fail naming each with its id, none fails saying so. The names matched are the People API's current names from the cache file; on the cache path they also include the frozen prose spellings the mirror holds (the `@name` of each task creation and the text at the offset of each `USER_MENTION` annotation), derived from the mirror in one pass when a name is not otherwise found and held for the run only, so a person renamed in Chat still resolves by an old spelling there. A blocked space never matches by name. An email resolves from the emails in the cache file, else through the API's find-direct-message call, the id then being the other human in that DM. `--person` alone on `messages` and `attachments` means the direct-message space with that person, both sides; with `--space` it keeps only that person's rows.

### Permissions the naming needs

Those three sources need `contacts.readonly`, `contacts.other.readonly` and `directory.readonly`, and the signed-in account's own name needs `userinfo.profile` (its profile being private to it); `login` mints them with the Chat scopes. A token minted before they existed lacks them. A command that finds its token short of a scope it needs runs the same consent flow `login` runs, adding to the scopes already granted (`include_granted_scopes`), then carries on with the new token: most people who upgrade never type `login` again. It runs only when someone can answer it: with a display it opens a browser, at a terminal without one it prints the link, and with neither (cron, CI, a headless MCP host) it does not start. It waits a bounded time. Declined, failed, unanswered or not started, the naming falls back to ids and one line says the saved login lacks the permission and to run `majordomo login`. The refusal is not remembered, so the next command that needs the scope asks again; within one command the resolver's single batched lookup asks once. Everything the flow prints goes to stderr and the browser is launched with its output discarded, because stdout is the MCP protocol stream. Under `WORLD_AS_OF` the flow never starts. The flow and the note live in the core, so the CLI prints the note on stderr and the MCP server carries the same words in its answer's `notes`.

## The sieve

The sieve is an allow-list and block-list of spaces. Its purpose is to keep certain conversations out of the agent's view: a user who chats with the tool through MCP should not have private spaces returned by `list_spaces` or scanned by `read_messages`. The lists live in the human-authored TOML, are loaded at the start of every call, and are applied inside the core before any space identifier or message reaches the caller.

Placing it in the core, not in a wrapper, means any front door (and any future front door) inherits it for free; a future automation that talks to the core directly cannot work around a wrapper-only gate.

## Sending

`send` posts a message to a space (`--space`, an id or a display name), a reply into a thread, or a message into a person's existing 1:1 DM (`--to`, a `users/<id>`, an email or a name, resolved by the API's find-direct-message call), carrying message text, one or more file attachments, or both. It works through both front doors, always over the direct Chat API (a write has no cache path). The write side follows the same discipline as the reads:

- The sieve applies to writes: a send into a blocked space is refused with the same wording as a space that does not exist, so a caller cannot probe the block list through send.
- A set `WORLD_AS_OF` refuses the send outright: a bounded run is a replay, and a send would act in the real present.
- `login` mints the send scope (`chat.messages.create`) together with the read scopes, so one token serves every path; a token without it goes through the consent flow in "Permissions the naming needs", and the send is refused with a pointer at `majordomo login` when that is not completed.
- An attachment is a local file, uploaded to the resolved space through the API's `media.upload` and referenced in the created message. The space is resolved and sieve-cleared before any upload, so a blocked or absent target is refused before a file leaves the machine. The upload rides the same `chat.messages.create` scope, so an attachment needs no scope the send did not already hold. When a file is attached the message text is optional (Chat carries an attachment-only message), and at least one of text or attachment is required.

## Attachments

`attachments` reports the files posted in a space, a thread, on one message, or by a person (`--person` alone being the direct-message space with them, and with `--space` only the files they posted there), and with `--download` saves them into a directory. It works through both front doors and always over the direct Chat API: the mirror carries message text, not files, so there is no cache path to offer.

That is also why the capability sits beside `send` in the core rather than inside the reader seam. The seam exists so the cache and the API answer interchangeably; here they cannot, and a `CacheReader.attachments` could only ever refuse. The sieve is applied in the same function instead, so neither front door reaches a blocked space.

- Naming one message fetches that message alone, rather than paging its whole space. Naming a space or a thread reads the files out of the ordinary paged message read: Chat's message list carries each message's attachment field, so the listing costs no fetch per message.
- The read scope already covers the download (`chat.messages.readonly` governs both the message list and the media fetch), so an existing token needs no re-consent.
- `WORLD_AS_OF` bounds this like any read rather than refusing it as it refuses `send`: a file posted after the bound is not reported, and one posted before it is part of the replay.
- The filename comes from whoever posted the file, so it is untrusted: it is stripped to a bare basename before it joins the destination directory, and a name with nothing left after stripping fails loud rather than being invented, an unmatchable download being worse than a stop.
- An existing file at the target path is left as it is and named. That also catches the case of two attachments on one message sharing a filename.
- A Drive-backed attachment carries a Drive reference in place of Chat file data; majordomo holds no Drive scope, so it lists but does not download, and says which file and why.

## Domain or consumer, and who owns a space

`spaces` reports whether each space belongs to a Google Workspace domain or a consumer/personal account, and, with `--owner`, who owns it. Both come from the Space and Membership resources of the Chat API ([reference](https://developers.google.com/workspace/chat/api/reference/rest/v1/spaces)), read fresh on every call rather than mirrored, since the BI cache holds neither:

- **Domain or consumer** comes from `Space.customer` (a Workspace customer id, e.g. `customers/C0xxxxxxx`), which lands in the same `spaces.list`/`spaces.get` call that already reads `displayName` and `spaceType`: no extra API call, no extra scope beyond `chat.spaces.readonly`. Its absence means a consumer/personal account created the space. A `DIRECT_MESSAGE` space carries no `customer` either way, so the field is meaningful for named spaces and group chats, not DMs. `Space.externalUserAllowed` rides along for free too.
- **Who owns it** comes from `Membership.role = ROLE_MANAGER` on `spaces.members.list`. Chat's API names this role `ROLE_MANAGER`, but its own UI calls it "Owner"; the API's `ROLE_ASSISTANT_MANAGER` is the separate role the UI calls "Manager", a different thing. Listing members needs `chat.memberships.readonly`, which `login` mints with the other scopes; a token without it goes through the consent flow above, and refuses `--owner` with a pointer at `login` when that is not completed. Under plain user auth the API gives back only the owning member's `users/<id>`, not a display name, so `--owner` names the owner the way every other person is named: through the one resolver.
- `--owner` costs one `spaces.members.list` call per space, on top of the one `spaces.list` call `spaces` always makes, so it stays opt-in rather than the default.

## Naming and packaging convention

The distribution name on PyPI and the Debian package name follow the lowercase-hyphen convention (`majordomo`). The import package uses underscores because hyphens are not valid in Python identifiers; for a single word the two are the same.

The `google-` prefix is avoided as a brand choice. In the apt ecosystem `google-*` is in practice Google's own namespace, and the community convention for third-party tools targeting a Google product is a brand-neutral or `g`-prefixed name with the product cited in the description (nominative fair use).

## Relationship to other tools

### crude and courier

majordomo is an information-flow reader and reporter, not an object-edit tool, so it lives in neither of the user's neighbouring accessors. crude is rejected as a home: its CRUD/object grammar does not fit a read-mostly message flow whose tasks are reconstructed, not stored. courier is the architectural template, not the host: its sieve and provenance-tagged cache-with-direct-API-fallback carry across, but its email-specific maildir+mu cache does not. The cache is what makes bulk reads fast, since Google throttles direct reads (a hundred-plus-item scan can take minutes, and users expect courier speed). It is an accelerator for sites that already run the mirror, not a requirement of the software, which reads Google directly wherever the mirror is absent. majordomo does not build it: it reads the BI platform's existing server-side `googlechat` mirror and `coord_tasks` reconstruction (direct DB first, an API later), keeping its own direct read and task decoder to run without that backend. How it serves callers, a query CLI or a daemon over REST in the Evolution API style, is open. `DATA-MODEL.md` holds the full reasoning.

### gchat-cli

`gchat-cli` (the project `chadsaun/gchat`, MIT) already implements the accessor layer majordomo needs: OAuth with multi-account support, TOML configuration, listing spaces, reading and searching messages, sending, and JSON output. It is a single-author build of about twenty hours from January 2026 with no activity or users since, so it is best treated as MIT source to fork and own rather than as a maintained dependency.

gchat-cli matters only for majordomo's standalone direct read, the fallback when the BI cache is absent, not the fast path, which reads the existing mirror. Forking its accessor for that fallback would save rebuilding OAuth, multi-account, and paginated message reads; what it does not do is majordomo's own work either way: assignee reporting, the sieve, the MCP interface, identity-keyed reporting, and automation.

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
- Send reachable through both front doors: one command shape (`send --space|--thread TEXT`), the sieve refusing blocked targets indistinguishably from missing ones, refused entirely under `WORLD_AS_OF`, its scope minted by every `login`.
- Attachments (listed, and saved with `--download`) reachable through both front doors, always over the direct API because the mirror carries no file rows, and so placed beside `send` in the core rather than in the reader seam, with the sieve applied in the same function. Bounded under `WORLD_AS_OF` as a read rather than refused as `send` is.
- Reading Google directly is the baseline every install can do, so the Google client libraries are core dependencies and the cache driver is the `bi` install option. The reverse would ship public software whose one usable path was optional.
- The direct-API module and config table are named `api`; `nocache` names only the read mode.
- People are named through the People API by one resolver in the core, over the profile, contact and other-contact sources, a profile name ahead of a contact label; what it fetched is a refetchable cache under `$XDG_CACHE_HOME/majordomo/` in the APIs' own form.
- A token short of a scope a command needs gets Google's consent flow from that command, when someone can answer it, rather than a pointer at `login`: the people upgrading are not all at home on a command line.
- Orchestration kept external.
- The accessor's data model is an information flow (crude's object-edit model is rejected). The fast path reads the BI platform's existing cache and reconstructed tasks, preferred wherever it exists because Google throttles direct reads, while majordomo's own direct read and decoder are what every install has. Delivery (query CLI versus REST daemon) is open (`DATA-MODEL.md`).

Open:

- Whether to fork and vendor gchat-cli as the accessor base, pending the pagination check.
- The concrete shape of the automatic processing (which message classes get which actions, where the agent loop lives).
- The packaging skeleton (Python project layout, the `pyproject.toml` shape, the Debian `debian/` files).

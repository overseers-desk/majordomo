# majordomo: design

## What majordomo is

majordomo is a command-line tool that reads Google Chat, reports on it, and sends messages into it. The command line is the primary interface; an MCP server is a secondary interface for AI agents. Both are thin front doors over one shared core, and the core is written so the same design can later reach sources other than Google.

Its name is the household steward who runs a principal's affairs and decides what reaches them. The choice points at the access filter described below, and the word is unclaimed on PyPI and apt as of 2026-05-22. Names beginning with `google-` are avoided because that namespace reads as official Google software, and `gchat` and `gchat-cli` are already taken and imply a generic chat client rather than this reporter.

## The problem

### Task reconstruction

1. The Google Tasks API returns a task assigned in a Chat space only to its assignee, and only when `tasks.list` is called with `showAssigned=true`; without the flag it returns none of them.
2. No Tasks API call returns a task assigned to someone else or names who assigned a task: `assignmentInfo` carries only `linkToTask`, `surfaceType`, `spaceInfo` and `driveResourceInfo`.
3. Who holds which task across people, and who assigned it, is therefore recoverable only from the Chat messages, which is why majordomo reconstructs tasks from messages.
4. For the signed-in person's own tasks, the Tasks API carries what messages lack: notes, due date, and the title of a task created fresh rather than from a message.
5. A task made from a Chat message links to that message's thread (`linkToTask` carries space and thread), which joins majordomo's row exactly; a task created fresh in a space links only to the space.

(Verified 2026-09-28 by `tasks.list` with and without `showAssigned`, against one account and Google's `tasks` reference.)

`tasks --to-me` fills its rows from that list with the notes, the due date, and a title where the messages yielded none: a task made from a message joins its row by thread; a task created fresh in a space joins by space and a creation time within a few seconds (`readers.OWN_MATCH_SECONDS`) of the task's `updated`, when exactly one task is that close (a fresh task edited later no longer matches, the API recording no creation time). The list is read under its own scope, `tasks.readonly`, asked for up front like the others.

The BI platform already does this reconstruction server-side (a `coord_tasks` table over a `googlechat` mirror); majordomo reports over it when that backend is present and runs its own message decoder when it is not. Which source serves which path is in `DATA-MODEL.md`.

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
- Act as a configured Chat app by identity, without swapping configuration files: send as it, and read spaces, tasks and messages as it under the sieve.
- Emit JSON for scripting alongside human-readable output.

## Architecture

### Core

One importable Python package holds the entire business behaviour: a reader for the BI platform's cache (the fast path) and a Google Chat client wrapping the official API (the standalone path), the configuration loader, the sieve, the task decoder, reporting, the choice of credentials (the personal login, credentials handed in, or a Chat app's key), and the person and space resolver with its cache files (roster.py). The cache reader is the primary source and the direct-API client its fallback; the backend accelerates, it does not gate. Three read modes follow: `cache` (the mirror), `nocache` (the direct API, no cache), and `live` (up-to-dateness). The `live` mode serves the cache and tops it up from the API for records newer than each space's cache watermark, polling only recently-active spaces so an unscoped freshness read stays within the read quota. "live" names the need (currency), not a mechanism; the cache bypass is `nocache`. The core has no command-line parsing and no MCP protocol code; it exposes functions and types that any caller can use.

A refusal is raised as `MajordomoError` (`errors.py`, with `NotFound` and `ScopeError` beneath it), never as a process exit.

The package's exported names (`majordomo.__all__`) are its entry points for a program that imports it: `load_config` builds the configuration, `tasks` and `messages` read through the reader seam (`make_reader`, which they call, picks the backend and reports which one served), `send` posts, `drain_notes` collects what the core had to say, and `MajordomoError`, `ScopeError` and `NotFound` are what it raises; `majordomo.dates.resolve` turns a window into bounds. The reads and `send` read only what they are handed: the configuration, with the `WORLD_AS_OF` bound inside it, the credentials (`credentials=` on a read, else the token file or Chat app key the configuration names), and the directory of the name cache (`cache_dir=`). Importing the package reads nothing under the home directory and loads neither front door.

### Front doors

Two front doors call the core and add no business logic:

- The command-line interface is the primary one. It is what a person types at a terminal, what a cron entry or systemd timer triggers, and what an automation script drives.
- The MCP server is the secondary one. It exposes the same operations as MCP tools so AI agents can call them through that protocol.

What they do around the core lives in `frontdoor.py`, which both call and the core never imports: reading the user's own config files and the environment's `WORLD_AS_OF` into the config a command runs with (see "Where the configuration comes from"), and Google's consent flow, which needs a browser or a person at a terminal. The core reads and uses every credential a read or send runs with; only the flow that mints a token lives in `frontdoor.py`. Because the sieve and the credentials live in the core, behaviour stays consistent across every front door, a single change reaches all of them, and no front door can bypass the access gate by accident. This is the first invariant in [`INVARIANTS.md`](INVARIANTS.md).

## Configuration

### The configuration files

The user's own files sit in the tool's config directory (`~/.config/majordomo/`):

- `config.toml`, human-authored, holding what a person edits by hand: the configured subject (`[me]`), the sieve's block lists (`[sieve]`), where the OAuth files sit (`[api]`), the Chat apps a command may act as (`[identity.NAME]`), and the cache database's settings (`[db]`) when they are kept there.
- `.env`, the cache database's `MYSQL_*` settings as the BI project holds them, which the front doors merge into `[db]`.
- `client_secret.json`, the OAuth client as Google issues it, and `token.json`, the token `login` writes, which the program rewrites on every refresh.

The split exists because the volatile and the stable should not share a file. If the token sat in the TOML, an automatic refresh would have to rewrite a file the person edits by hand; comments and formatting would not survive, and concurrent edits could race. Keeping the rewritten file apart makes the program and the person each own their own surface.

Reading is per-account OAuth: each account signs in through Google's browser consent flow and majordomo reads as that user. A service account is a non-human identity that cannot read a given user's Chat on its own, and domain-wide delegation, which can, reaches only accounts inside a Google Workspace domain that an administrator has enabled, excluding consumer Gmail; majordomo reads through neither. Per-account consent works for any account that can grant it, at the cost of one login per user. A service account has one use here: a Chat app's key, with which majordomo sends and reads as that app, the app seeing only the spaces it is a member of (below).

### Where the configuration comes from

The core works from the configuration it is handed. `config.load_config` builds it from a dict in `config.toml`'s shape, a path to a TOML file, or nothing, which reads `config.toml` from the config directory (`~/.config/majordomo/`, resolved when called, so importing majordomo reads nothing under the home directory). A dict is copied, so the caller's own is never changed, and taken literally: a library caller names every path itself. A file's `[api]` token and client files default to `token.json` and `client_secret.json` beside it, and `~` in its `[api]` paths and in an identity's `service_account_file` is the home directory. Every source is checked for the shape majordomo reads: `me`, `sieve`, `api`, `identity` and `db` are tables and the sieve's lists are lists of strings, since a sieve list read as anything else would let a blocked space through; keys majordomo does not know are kept.

The cache database's settings are the `[db]` table, the `MYSQL_*` keys. The front doors read the user's config through `frontdoor.load`, which merges the `.env` beside `config.toml` into `[db]`, a key `[db]` sets itself winning. A config whose `[db]` lacks a setting leaves the cache unavailable, which the reader seam treats as it treats a database that is down: the default read falls back to the Chat API and `--cache` refuses.

The `WORLD_AS_OF` bound travels in the configuration too. `load_config` takes it as `world_as_of`, an ISO-8601 string with a timezone offset or an aware datetime, and parses it before anything else, so a bad bound is the refusal a command meets whatever else is wrong with it. Every read takes the bound from the configuration, never from the environment, so a library caller is bounded by what it hands in. `frontdoor.load` hands in the environment's `WORLD_AS_OF`, and the MCP server loads on every tool call, so a bad bound fails only that call rather than the server.

### The name cache

One more file kind sits apart from the configuration files: the people and spaces majordomo has fetched, which the front doors keep under the XDG cache directory (`$XDG_CACHE_HOME/majordomo/`, `~/.cache/majordomo/` when unset, on Linux and macOS alike). A library call keeps them in the `cache_dir` it is handed, or, handed none, keeps no file and fetches the names each call needs. Two files, `people.json` and `spaces.json`, each a single JSON object keyed by resource name (`people/<id>`, `spaces/<id>`) whose value is `{"fetched_at": <ISO time>, "person"|"space": <the API object as returned>}`: the People API's `Person` and the Chat API's `Space`, a direct-message space returned by `findDirectMessage` included. Everything in it is refetchable, so it is a cache, and storing the API's own form avoids a private schema. A file that changed is rewritten whole through a temporary file and a rename, so a reader never sees half of one. A person fetched more than thirty days ago is fetched again when next shown, so a rename reaches the reports. Under `WORLD_AS_OF` the cache is read and never written.

### Acting as a Chat app

Credentials are keyed by identity, so a command acts as a configured Chat app by naming it from any front door rather than by swapping files. The shape is a `[identity.NAME]` table mapping a name to its credentials, which scales to as many apps as a person configures; a command chooses one with `--as NAME` on the CLI or `identity` on the MCP `send`, `spaces`, `tasks` and `messages` tools.

An identity holds a Chat app's service-account key (`service_account_file`). `api.chat_credentials` is the one place a Chat read's credential is chosen: credentials handed in, else under an identity the app's key with `chat.bot` and `chat.app.messages.readonly`, else the saved login. `send` asks the key for `chat.bot` alone, which needs no administrator's approval, and posts as the app into spaces it is a member of, `--to` reaching only the app's own DM with that person; Google accepts file uploads only from a person's login ([`media.upload`](https://developers.google.com/workspace/chat/api/reference/rest/v1/media/upload)), so `send` refuses attachments under it.

Under an identity `spaces`, `tasks` and `messages` read the Chat API as the app, under the sieve. The mirror is a person's view of Chat, not the app's (the BI connector fills it through a user's OAuth token), so `make_reader` reads directly by default and refuses `--cache` and `--live`. As the app, `spaces.list` returns only the spaces it is a member of and `findDirectMessage` the app's own DM with a person, so `--person` alone reads that DM; `spaces.messages.list` returns public messages only, never a private message an app showed to one person ([reference](https://developers.google.com/workspace/chat/api/reference/rest/v1/spaces.messages/list)). Listing messages as an app needs `chat.app.messages.readonly`, which a Google Workspace administrator approves for the app once ([Google's steps](https://developers.google.com/workspace/chat/authenticate-authorize-chat-app)); Google answers 403 both before that approval and for a space the app is not a member of, so a 403 on that read under an identity is raised as a `ScopeError` naming the identity and both causes.

The app has no People API and no Google Tasks list, so under it the People and own-task lookups are absent rather than failing: people are named from the name cache where it holds them, else from the `displayName` the Chat API puts on every `User` it returns to an app caller ([reference](https://developers.google.com/workspace/chat/api/reference/rest/v1/User)), held for the run and never stored; and `tasks --to-me` rows carry no notes or due dates, with no note, there being no login to fix. Under an identity, each capability that serves only a person's login refuses for itself, naming the identity: the people report, `spaces --owner`, `attachments`, and `login`.

## Naming people and spaces

A message names its sender by `users/<id>` and nothing else, in the mirror and from the Chat API under a person's login alike (the API fills a `User`'s `displayName` for an app caller, and for a person's login only in some cases); the user table carries no display name or email, and membership is not mirrored. The People API names the person: a Chat `users/<id>` is the People API's `people/<id>`, read with `people.getBatchGet` ([reference](https://developers.google.com/people/api/rest/v1/people/getBatchGet)), up to 200 ids per call, asking `personFields=names,emailAddresses,metadata`.

The People API consults only the sources a request names, and unset it reads the profile and saved contacts alone. majordomo names three ([`ReadSourceType`](https://developers.google.com/people/api/rest/v1/ReadSourceType)): `READ_SOURCE_TYPE_PROFILE` (which returns the Google profile, the Workspace domain profile and the account), `READ_SOURCE_TYPE_CONTACT` (the signed-in account's saved contacts), and `READ_SOURCE_TYPE_OTHER_CONTACT` (its "other contacts", the people it has interacted with but not saved). The last is the only source that names some colleagues, and a consumer account's public profile often carries no name at all. When several sources name a person, the profile or domain-profile name wins over a contact label, a contact label being the signed-in user's own naming of someone. The same answer carries email addresses, which is how an email resolves to an id without a Chat call.

The one resolver lives in the core (roster.py) and every report that shows a person goes through it: task assignees on both paths, message and attachment senders, space owners under `spaces --owner`, and `people`. It reads the cache file first, asks the People API in batches for ids not there and writes the answers back, and for an id neither names it serves the `displayName` the Chat API put on the user this run, held for the run only. `block_assignees` is applied both to the name a row came with and to the resolved name, so a person blocked by either stays out. An id the API cannot name, or a run whose token cannot reach it, shows the bare `users/<id>`; the report still answers.

Every command that takes a person (`--person`, `--assignee`, `--to`) accepts `users/<id>`, an email, or a name; every command that takes a space accepts `spaces/<id>` or its display name. Resolution runs inside the core, in the reader or in the send and attachments functions, so both front doors pass the string through and neither can bypass the sieve on the resolved space. A name is matched case-insensitively, whole name first and then as a substring, and must match one person: several fail naming each with its id, none fails saying so. The names matched are the People API's current names from the cache file; on the cache path they also include the frozen prose spellings the mirror holds (the `@name` of each task creation and the text at the offset of each `USER_MENTION` annotation), derived from the mirror in one pass when a name is not otherwise found and held for the run only, so a person renamed in Chat still resolves by an old spelling there. A blocked space never matches by name. An email resolves from the emails in the cache file, else through the API's find-direct-message call, the id then being the other human in that DM. `--person` alone on `messages` and `attachments` means the direct-message space with that person, both sides; with `--space` it keeps only that person's rows.

### Permissions and when consent is asked

Those three sources need `contacts.readonly`, `contacts.other.readonly` and `directory.readonly`, and the signed-in account's own name needs `userinfo.profile` (its profile being private to it); `login` mints them with the Chat scopes. A token minted before they existed lacks them.

Every command's arguments say, ahead of time, which Google scopes it will touch: reading the Chat API directly (`--live`, `--nocache`, and the paths that read it directly in every case regardless of the source flags: `send`, `attachments`, `spaces --owner`), `--owner`'s membership lookup, `send`, showing a resolved person's name (a People-scope need that holds even on a cache-only read), and `tasks --to-me`'s fill from the signed-in person's own task list. `api.ensure_command_scopes` is the one core function that reads that shape and names the union of whatever the saved token lacks, raised as one `ScopeError` carrying the missing scopes and what each group is for. It raises nothing for a shape that needs nothing, for an install with no saved token at all (an install that never logged in meets the "no OAuth token at …" error `get_credentials` raises, which sends it to `majordomo login`, rather than a consent page it did not ask for), under an identity, the Chat app having no consent flow, or under `WORLD_AS_OF`, a bounded run asking no consent. Both front doors run it at the very start of every command and tool, through `frontdoor.up_front`: before the first Chat or People call, and before `spaces --owner`'s membership reads, which back off under Google's per-minute quota for the better part of a minute, so a decline costs nothing already spent reading.

The consent flow lives in the front doors (`frontdoor.py`): a core call must never drop a library caller into an interactive flow nobody can answer (a cron job has no terminal), so the core raises `ScopeError` and the front door decides. The core never runs the flow and never imports that module. On the up-front `ScopeError`, `up_front` runs the flow `login` runs, adding to the scopes already granted (`include_granted_scopes`), then checks once more: most people who upgrade never type `login` again. It runs only when someone can answer it: with a display it opens a browser, at a terminal without one it prints the link, and with neither (cron, CI, a headless MCP host) it does not start, nor for an identity, the app having no consent flow. It waits a bounded time. Everything the flow prints goes to stderr, and the browser is launched with its output discarded, because stdout is the MCP protocol stream.

Declined, failed, unanswered or not started, the command carries on, and each feature that needs a scope checks for it itself (`api.ensure_scopes`): naming people (`people_service`) and the `--to-me` fill (`own_tasks`) fall back softly, to `users/<id>` and to rows without notes, with one line saying the login lacks the permission and that `majordomo login` grants it to the saved one; `send` refuses with that line as a `ScopeError`, and `--owner` at its first membership read. Since only the front door asks Google, a command asks at most once. Across commands nothing is remembered: the next command, or the next MCP tool call, asks again, because a decline is a fact about that one attempt, not about the account.

A failed consent attempt reports a safe reason alongside the fallback line: the exception's class name, and a plain sentence for the cases majordomo tells apart (a timeout, the loopback port already in use, a decline at Google's own page, a missing OAuth client file). It omits the exception's message, its arguments, and its traceback, since a failed OAuth exchange can carry the client secret and the authorization code in them.

The core prints nothing. A line it has for the person (a soft fallback, a clamped `--until`, a store that does not reach the `WORLD_AS_OF` bound) waits in `config.note_once` until a front door drains it: the CLI prints the notes on stderr after the up-front check, before a refusal's line, and at the command's end; the MCP server carries the same words in its answer's `notes`. A refusal reaches an MCP client as its one line, raised as the SDK's `ToolError`, which the SDK shows where it hides any other exception's text.

## The sieve

The sieve is an allow-list and block-list of spaces. Its purpose is to keep certain conversations out of the agent's view: a user who chats with the tool through MCP should not have private spaces returned by `list_spaces` or scanned by `read_messages`. The lists live in the human-authored TOML, are loaded at the start of every call, and are applied inside the core before any space identifier or message reaches the caller. `make_reader` (behind `tasks` and `messages`) and `send` take the lists from the configuration they are handed, with no list passed beside it, so a program importing majordomo cannot hand them one that blocks less.

Placing it in the core, not in a wrapper, means any front door (and any future front door) inherits it for free; a future automation that talks to the core directly cannot work around a wrapper-only gate.

A blocked space named explicitly answers "not found", in the same words as a space that does not exist, on every read source and on `send` and `attachments`. An absent one is refused too, because refusing only the blocked one would leak the block list on the cache path, where an absent id would otherwise come back as an empty answer. The cache judges absence by the mirror's space table and the direct API by Chat's 404; `--live` reads a space the mirror lacks over the API before calling it absent. A thread is judged by its space, and a thread missing from a space that exists is an empty answer.

## Sending

`send` posts a message to a space (`--space`, an id or a display name), a reply into a thread, or a message into a person's existing 1:1 DM (`--to`, a `users/<id>`, an email or a name, resolved by the API's find-direct-message call), carrying message text, one or more file attachments, or both. It works through both front doors, always over the direct Chat API (a write has no cache path). The write side follows the same discipline as the reads:

- The sieve applies to writes: a send into a blocked space is refused with the same wording as a space that does not exist, so a caller cannot probe the block list through send.
- A set `WORLD_AS_OF` refuses the send outright: a bounded run is a replay, and a send would act in the real present.
- `login` mints the send scope (`chat.messages.create`) together with the read scopes, so one token serves every path; a token without it is asked up front, before anything else in the command runs (see "Permissions and when consent is asked"), and the send is refused with a pointer at `majordomo login` when that is not completed.
- An attachment is a local file, uploaded to the resolved space through the API's `media.upload` and referenced in the created message. The space is resolved and sieve-cleared before any upload, so a blocked or absent target is refused before a file leaves the machine. The upload rides the same `chat.messages.create` scope, so an attachment needs no scope the send did not already hold. When a file is attached the message text is optional (Chat carries an attachment-only message), and at least one of text or attachment is required.

## Attachments

`attachments` reports the files posted in a space, a thread, on one message, or by a person (`--person` alone being the direct-message space with them, and with `--space` only the files they posted there), and with `--download` saves them into a directory. It works through both front doors and always over the direct Chat API: the mirror carries message text, not files, so there is no cache path to offer.

That is also why the capability sits beside `send` in the core rather than inside the reader seam. The seam exists so the cache and the API answer interchangeably; here they cannot, and a `CacheReader.attachments` could only ever refuse. The sieve is applied in the same function instead, so neither front door reaches a blocked space.

- Naming one message fetches that message alone, rather than paging its whole space. Naming a space or a thread reads the files out of the ordinary paged message read: Chat's message list carries each message's attachment field, so the listing costs no fetch per message.
- The read scope already covers the download (`chat.messages.readonly` governs both the message list and the media fetch), so an existing token needs no re-consent.
- `WORLD_AS_OF` bounds this like any read rather than refusing it as it refuses `send`: a file posted after the bound is not reported, and one posted before it is part of the replay.
- The filename comes from whoever posted the file, so it is untrusted: it is stripped to a bare basename before it joins the destination directory, and a name with nothing left after stripping is refused rather than invented, an unmatchable download being worse than a stop.
- A download never overwrites. An existing file at the target path, or an earlier attachment in the same run with the same posted name (Chat posts pasted images all as "untitled"), is left as it is, and the new file is written beside it as name-2, name-3; the row's `path` says which file is which. A name without an extension takes one from the content type.
- A file that cannot be saved (Drive-backed, no usable name) gets the reason on its row under `error` and the rest are still fetched; the CLI exits 1 when any row carries one.
- A Drive-backed attachment carries a Drive reference in place of Chat file data; majordomo holds no Drive scope, so it lists but does not download, and says which file and why.

## Domain or consumer, and who owns a space

`spaces` reports whether each space belongs to a Google Workspace domain or a consumer/personal account, and, with `--owner`, who owns it. Both come from the Space and Membership resources of the Chat API ([reference](https://developers.google.com/workspace/chat/api/reference/rest/v1/spaces)), read fresh on every call rather than mirrored, since the BI cache holds neither:

- **Domain or consumer** comes from `Space.customer` (a Workspace customer id, e.g. `customers/C0xxxxxxx`), which lands in the same `spaces.list`/`spaces.get` call that already reads `displayName` and `spaceType`: no extra API call, no extra scope beyond `chat.spaces.readonly`. Its absence means a consumer/personal account created the space. A `DIRECT_MESSAGE` space carries no `customer` either way, so the field is meaningful for named spaces and group chats, not DMs. `Space.externalUserAllowed` rides along for free too.
- **Who owns it** comes from `Membership.role = ROLE_MANAGER` on `spaces.members.list`. Chat's API names this role `ROLE_MANAGER`, but its own UI calls it "Owner"; the API's `ROLE_ASSISTANT_MANAGER` is the separate role the UI calls "Manager", a different thing. Listing members needs `chat.memberships.readonly`, which `login` mints with the other scopes; a token without it is asked up front, before the first membership read (see "Permissions and when consent is asked"), and refuses `--owner` with a pointer at `login` when that is not completed. Under plain user auth the API gives back only the owning member's `users/<id>`, not a display name, so `--owner` names the owner the way every other person is named: through the one resolver.
- `--owner` costs one `spaces.members.list` call per space, on top of the one `spaces.list` call `spaces` always makes, so it stays opt-in rather than the default.

## Naming and packaging convention

The distribution name on PyPI and the Debian package name follow the lowercase-hyphen convention (`majordomo`). The import package uses underscores because hyphens are not valid in Python identifiers; for a single word the two are the same.

The `google-` prefix is avoided as a brand choice. In the apt ecosystem `google-*` is in practice Google's own namespace, and the community convention for third-party tools targeting a Google product is a brand-neutral or `g`-prefixed name with the product cited in the description (nominative fair use).

## Relationship to other tools

### crude and courier

majordomo is an information-flow reader and reporter, not an object-edit tool, so it lives in neither of the user's neighbouring accessors. crude is rejected as a home: its CRUD/object grammar does not fit a read-mostly message flow whose tasks are reconstructed, not stored. courier is the architectural template, not the host: its sieve and provenance-tagged cache-with-direct-API-fallback carry across, but its email-specific maildir+mu cache does not. The cache is what makes bulk reads fast, since Google throttles direct reads (a hundred-plus-item scan can take minutes, and users expect courier speed). It is an accelerator for sites that already run the mirror, not a requirement of the software, which reads Google directly wherever the mirror is absent. majordomo does not build it: it reads the BI platform's existing server-side `googlechat` mirror and `coord_tasks` reconstruction (direct DB first, an API later), keeping its own direct read and task decoder to run without that backend. How it serves callers, a query CLI or a daemon over REST in the Evolution API style, is open. `DATA-MODEL.md` holds the full reasoning.

### gchat-cli

`gchat-cli` (the project `chadsaun/gchat`, MIT) already implements the accessor layer majordomo needs: OAuth with multi-account support, TOML configuration, listing spaces, reading and searching messages, sending, and JSON output. It is a single-author build of about twenty hours from January 2026 with no activity or users since, so it is best treated as MIT source to fork and own rather than as a maintained dependency.

gchat-cli matters only for majordomo's standalone direct read, the fallback when the BI cache is absent, not the fast path, which reads the existing mirror. Forking its accessor for that fallback would save rebuilding OAuth, multi-account, and paginated message reads; what it does not do is majordomo's own work either way: assignee reporting, the sieve, the MCP interface, acting as a Chat app, and automation.

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
- A token short of a scope a command needs gets Google's consent flow from that command, when someone can answer it, rather than a pointer at `login`: the people upgrading are not all at home on a command line. Asked for up front, from the command's own shape, before any Chat or People call, rather than only when the first feature that needs it happens to run.
- Orchestration kept external.
- The accessor's data model is an information flow (crude's object-edit model is rejected). The fast path reads the BI platform's existing cache and reconstructed tasks, preferred wherever it exists because Google throttles direct reads, while majordomo's own direct read and decoder are what every install has. Delivery (query CLI versus REST daemon) is open (`DATA-MODEL.md`).

Open:

- Whether to fork and vendor gchat-cli as the accessor base, pending the pagination check.
- The concrete shape of the automatic processing (which message classes get which actions, where the agent loop lives).

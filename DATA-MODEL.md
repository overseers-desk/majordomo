# Data model: how Google Chat access is structured

A decision record for **majordomo**, the command-line tool that reads Google Chat and reports task activity (see `DESIGN.md` and `PLAN.md` for what it is). It settles which kind of tool majordomo is, where it sits relative to the user's neighbouring accessors, and how it relates to the server-side cache that already mirrors Chat. This file is the fuller record of the decision; `DESIGN.md`'s "Relationship to other tools" section carries the bottom line and points here.

## The question

majordomo reads Google Chat and reports task activity. The accessor layer it needs — OAuth, list spaces, read and search messages over a date window, paginate to completeness, emit JSON — is generic plumbing that two of the user's existing tools already resemble:

- **crude**: a monorepo of per-site CLIs under one grammar, `crude-<site> <resource> <verb>`, for read/write access to your own data on sites that lack a usable public API (ATDW, Skål, Rezdy, Deputy).
- **mailroom**: a CLI-and-MCP accessor over IMAP email, with an optional local cache for fast archive-grade search, a privacy sieve, and identity-keyed multi-account.

So the accessor could be a third crude site, a standalone tool (the `DESIGN.md` plan), or an extension folded into mailroom. Underneath that placement question sits a data-model question that decides it: is conversation access fundamentally an **object-edit** problem (crude's model), an **information-flow** problem (a stream of messages over time), or an **information-repository** problem (a locally cached, queryable mirror, mailroom's model)?

## What the existing tools show

**mailroom's cache is not mailroom's to lend.** mailroom owns no message database. An external syncer (offlineimap or mbsync) keeps a maildir on disk; `mu` indexes that maildir into a Xapian database; mailroom only reads the index (`mu find --format=json`) and the maildir files, falling back to live IMAP — each result tagged by provenance — whenever the index is missing, stale, or cannot serve the query. mailroom runs neither the syncer nor `mu index`; its contract is "a maildir exists and mu indexes it" (mailroom repo: `LOCAL_CACHE.md`, `local_cache.py`). Every piece — maildir, the IMAP UID embedded in the mbsync filename, mu — exists because email standardised it decades ago. Google Chat has none of them: its messages are JSON from a REST API, not RFC822 files in a maildir. Folding chat into mailroom would therefore inherit no cache. The chat mirror is a separate store, and one already exists server-side: the BI platform's `googlechat` connector mirrors Chat into MySQL `googlechat_*` tables, on an hourly cursor sync.

**The connector ecosystem is uniformly pull, one per source.** The user's accessors — mailroom over IMAP, and the MCP servers for Facebook, Instagram, Deputy, and Google Calendar — are each scoped to a single source and answer a request with a result. None streams; none shares a cache with another.

## The data model

**Object-edit (the crude model): rejected.** A crude site has a stable server-side object you mutate — a listing, a roster row, a product. A conversation has no such object. Messages are append-only and effectively immutable to an accessor, and the entity majordomo cares about — a task — is reconstructed from message patterns and stored on no server, so there is nothing to create, update, or delete. crude's `<resource> <verb>` grammar would model a thing that is not there.

**Information-flow: the irreducible core.** majordomo is, at bottom, a reader of a time-ordered, sender-attributed message flow over a date window. This shape is medium-neutral and is the part that extends to WhatsApp, Messenger, or any later source. It matches every connector the user already runs. How it reads that flow — from a cache or directly from Google — is the next question; that it is a flow, not an editable object store, is the settled core.

**Information-repository (cache): mandatory, and it already exists server-side.** Google throttles read access at a rate roughly linear in volume: scanning a hundred-plus tasks or messages against the API directly can take five to ten minutes, the slowness being how Google meters access rather than a transient. Users expect the speed mailroom gives. So cache-first is the primary use-case, not an optimisation. majordomo does not build this cache. A server-side mirror already runs: the BI platform's `googlechat` connector pulls Chat through a long-lived user-OAuth token on an hourly cursor sync, walking every space the account is in — group spaces and direct messages alike — forward to the present and back to a twelve-month floor, keeping each message's verbatim JSON in MySQL `googlechat_*` tables. Task reconstruction already runs above it, decoding the "Created a task for @Person" messages into a `coord_tasks` table. majordomo reads that store — direct database access first, a served API later — and reports over it. This is mailroom's contract: what carries across is the contract, not the store, and the store here is the existing chat mirror, not maildir+mu.

**Self-sufficient without the backend.** The BI cache is the fast path, not a dependency. majordomo must run where that backend is absent, so it keeps two capacities of its own: a direct pull from the Chat API (slow under the throttle, but complete), and its own decoder — the capacity to turn "Created a task for @Person" messages into task descriptions. When the BI platform is present, majordomo reports over its `coord_tasks` and `googlechat_*` mirror; when it is not, the same outputs come from majordomo's own direct read and its own reconstruction. The backend accelerates; it never gates.

**PM rides on user OAuth.** Direct messages are private to one account; a service account cannot read this workspace's messages or DMs without per-space admin grants, which is why the mirror impersonates a user. Two consequences follow: direct-message history is already in the cache (it is the larger half of it), and each Google identity majordomo serves is read through its own OAuth, its DM history a per-identity slice.

**Streaming is a use-case, not the foundation.** The cache is filled by the BI connector's hourly cursor sync, a windowed pull; majordomo reads the result. A real-time tail of incoming messages, an agent acting per message, is a further mode wanted by the automation ambition, and a daemon delivery (below) would subsume it. The data model does not require streaming; the sync cadence, not a real-time socket, is the freshness knob.

## Delivery: CLI or daemon over the cache

majordomo does not own the sync — the BI connector does, and the standalone fallback is a direct read. What is open is how majordomo serves its own callers, with at least two shapes.

- **A query CLI over the store.** A CLI reads the cache — direct database access first, its served API later — and emits JSON. No long-running process, and it matches the sibling tools (crude and mailroom are both CLIs). Scripting is the CLI's JSON rather than hand-rolled `curl | jq`.
- **A serving daemon over REST.** A long-running service reads the store, holds connections, and answers RESTful calls; scripting is `curl | jq`. This is the shape of Evolution API (`evolution-foundation/evolution-api`), the open-source WhatsApp REST server. It serves many clients and subsumes a real-time tail, at the cost of a service to run and monitor.

The daemon option connects to the "extend beyond Google" goal. When WhatsApp joins, its accessor is most likely Evolution API, itself a cache-plus-REST server; majordomo would then read several upstream caches — the BI `googlechat` store, an Evolution API store — behind one protocol-neutral surface. Extending Evolution API itself to host Google Chat is a poor fit: its spine is WhatsApp (Baileys, JIDs, the message proto, QR-paired per-phone instances), which Google Chat (spaces and users, Cards v2) does not fit without permanent contortion. The sources stay separate and are unified above, not folded into one another.

The two shapes are not mutually exclusive in time. The upstream cache is the invariant; a CLI reader and a daemon reader are both front doors over the same store. Building the CLI first, the cheaper path to the mandatory speed, does not foreclose adding a daemon later if WhatsApp as a peer, or multi-client access, makes the operational cost worth paying.

## Decision

- majordomo is an **information-flow reader and reporter**, not an editable object store (so crude is out). It reads a message flow and reports task activity over it.
- **The fast path is the BI platform's cache, read not built.** majordomo queries the `googlechat_*` mirror and the `coord_tasks` reconstruction — direct database access first, a served API later. Google's throttle is what makes cache-first mandatory for usable speed.
- **It is self-sufficient without that backend.** majordomo retains its own direct Chat read and its own task decoder, so it runs where the BI platform is absent; reconstruction is reused from `coord_tasks` when present and performed by majordomo's own decoder when not. The backend accelerates, it does not gate.
- **PM needs per-identity OAuth.** A service account cannot read this workspace's messages or DMs; each identity is read through its own OAuth, and direct-message history is already the larger half of the cache.
- **crude is not the home** (its CRUD/object model does not fit a read-mostly flow), and **mailroom is the template, not the host** (its contract carries across; its maildir+mu cache does not).
- **The sieve, identity-keyed multi-account, reporting, and the CLI/MCP front doors are majordomo's own work**, over whichever source — BI cache or direct read — serves the flow.
- How majordomo serves its callers — a query CLI over the store, or a serving daemon over REST in the Evolution API style — is **open**; the upstream cache is the invariant both front doors read, so the choice can be sequenced.

This settles that majordomo is a reader, reporter, and gate that uses the BI platform's chat cache and task reconstruction as a fast path while keeping its own direct read and decoder for standalone use. The `DESIGN.md` question of whether to fork `gchat-cli` now applies to that standalone direct path, and still rests on the pagination check recorded there.

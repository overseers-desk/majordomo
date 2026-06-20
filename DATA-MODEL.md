# Data model: how Google Chat access is structured

A decision record for **majordomo**, the command-line accessor that reads Google Chat and reconstructs task activity (see `DESIGN.md` and `PLAN.md` for what it is). It settles which kind of tool the Google Chat accessor is and where it sits relative to the user's two neighbouring accessors. This file is the fuller record of the decision; `DESIGN.md`'s "Relationship to other tools" section carries the bottom line and points here.

## The question

majordomo reads Google Chat and reconstructs task activity from messages. The accessor layer it needs — OAuth, list spaces, read and search messages over a date window, paginate to completeness, emit JSON — is generic plumbing that two of the user's existing tools already resemble:

- **crude** (`SmartLayer/crude`): a monorepo of per-site CLIs under one grammar, `crude-<site> <resource> <verb>`, for read/write access to your own data on sites that lack a usable public API (ATDW, Skål, Rezdy, Deputy).
- **mailroom** (`SmartLayer/mailroom`): a CLI-and-MCP accessor over IMAP email, with an optional local cache for fast archive-grade search, a privacy sieve, and identity-keyed multi-account.

So the accessor could be a third crude site, a standalone tool (the `DESIGN.md` plan), or an extension folded into mailroom. Underneath that placement question sits a data-model question that decides it: is conversation access fundamentally an **object-edit** problem (crude's model), an **information-flow** problem (a stream of messages over time), or an **information-repository** problem (a locally cached, queryable mirror, mailroom's model)?

## What the existing tools show

**mailroom's cache is not mailroom's to lend.** mailroom owns no message database. An external syncer (offlineimap or mbsync) keeps a maildir on disk; `mu` indexes that maildir into a Xapian database; mailroom only reads the index (`mu find --format=json`) and the maildir files, falling back to live IMAP — each result tagged by provenance — whenever the index is missing, stale, or cannot serve the query. mailroom runs neither the syncer nor `mu index`; its contract is "a maildir exists and mu indexes it" (mailroom repo: `LOCAL_CACHE.md`, `local_cache.py`). Every piece — maildir, the IMAP UID embedded in the mbsync filename, mu — exists because email standardised it decades ago. Google Chat has none of them: its messages are JSON from a REST API, not RFC822 files in a maildir. Folding chat into mailroom would therefore inherit no cache; a local-mirror stack would have to be built from scratch for chat wherever the code lived.

**The connector ecosystem is uniformly pull, one per source.** The user's accessors — mailroom over IMAP, and the MCP servers for Facebook, Instagram, Deputy, and Google Calendar — are each scoped to a single source and answer a request with a result. None streams; none shares a cache with another.

## The data model

**Object-edit (the crude model): rejected.** A crude site has a stable server-side object you mutate — a listing, a roster row, a product. A conversation has no such object. Messages are append-only and effectively immutable to an accessor, and the entity majordomo cares about — a task — is reconstructed from message patterns and stored on no server, so there is nothing to create, update, or delete. crude's `<resource> <verb>` grammar would model a thing that is not there.

**Information-flow: the irreducible core.** majordomo is, at bottom, a connector that reads a time-ordered, sender-attributed message flow over a date window, paginated to completeness. This shape is medium-neutral and is the part that extends to WhatsApp, Messenger, or any later source. It matches every connector the user already runs.

**Information-repository (cache): a mandatory layer over the flow, owned by majordomo.** Google throttles read access by serving results at a rate roughly linear in their volume: scanning a hundred-plus tasks or messages against the live API can take five to ten minutes, the slowness being how Google controls access rather than a transient. Users expect the speed mailroom gives them. The tool should still answer without a cache, and that fallback is worth keeping, but the cached path is the primary use-case, not an optimisation. What is worth taking from mailroom is its contract, not its code: a sync process maintains a local mirror, and the reader answers from the mirror but falls back to the live source, with a provenance tag, when the mirror cannot serve a query. That contract is medium-neutral; the implementation for chat — a local store of message JSON plus a sync job — is chat-specific and majordomo's own. Where mailroom keeps its cache opt-in because only archive-scale search justifies it, here the throttle makes the cache the default surface.

**Streaming is a use-case, not the foundation.** The cache is filled by a windowed pull on a schedule (a sync every few minutes is enough for the throttle problem above), and reconstruction and reporting read the cache. A live tail of incoming messages, an agent acting per message, is a further mode wanted by the automation ambition, and a daemon delivery (below) would subsume it. The data model does not require streaming; the sync cadence, not a live socket, is the freshness knob.

## Delivery: CLI over a synced cache, or a caching daemon

That the cache is mandatory is settled; how it is filled and served is open, with at least two shapes.

- **CLI over a synced cache.** A `sync` command, run from cron every few minutes, writes the local store; a separate query CLI reads it and emits JSON. This is mailroom's contract with majordomo owning the sync, and it matches the sibling tools (crude and mailroom are both CLIs). No long-running process, and scripting is the CLI's JSON rather than hand-rolled `curl | jq`.
- **A caching daemon over REST.** A long-running service syncs continuously, caches internally, and answers RESTful calls; an AI drives it with `curl | jq` taught by a skill, the same skill-as-bridge that drives the CLI. This is the shape of Evolution API (`evolution-foundation/evolution-api`), the open-source WhatsApp REST server. It subsumes streaming and serves many clients, at the cost of a service to run and monitor. The daemon's interface is REST plus that skill, not an MCP server: majordomo owns its JSON, so it has none of the code-shaped parts (foreign wire formats, fuzzy identifier resolution) that would justify wrapping the API in MCP rather than a skill.

The daemon option connects to the "extend beyond Google" goal, since Evolution API is the obvious WhatsApp accessor. Extending Evolution API itself to host Google Chat is a poor fit: its spine is WhatsApp (Baileys, JIDs, the message proto, QR-paired per-phone instances), and Google Chat (spaces and users, one service-account app serving many spaces, Cards v2) does not fit the instance model without permanent contortion. The viable daemon routes are an Evolution-API-*style* neutral daemon majordomo owns, or Evolution API run as-is for WhatsApp with a separate Google Chat service and a protocol-neutral cached layer above both.

The two shapes are not mutually exclusive in time. The cache database is the invariant; a CLI reader and a daemon reader are both front doors over the same store. Building the store, the sync, and the CLI first, the cheaper path to the mandatory speed, does not foreclose adding a daemon later if WhatsApp as a peer, or multi-client access, makes the operational cost worth paying.

## Decision

- majordomo is an **information-flow connector backed by a mandatory local cache**: a windowed pull fills the cache, and a reader answers from it with live fallback. Object-edit (crude) is the only model rejected.
- **crude is not the home.** Its object/CRUD model does not fit a read-mostly message flow whose valuable entity is reconstructed rather than stored. The crude-site candidate is rejected.
- **mailroom is the architectural template, not the host.** What carries across is the contract: a command line that an AI drives through a skill, a sieve, identity-keyed accounts, and a provenance-tagged local-cache-with-live-fallback. The cache itself does not carry across; it is email-specific.
- The local cache is **mandatory**, not deferred: Google's throttle makes the live path too slow for the expected experience. It copies mailroom's contract, but its implementation is chat-specific.
- Reconstruction, assignee and space reporting, and the sieve sit **above the cache** and stay medium-agnostic.
- How the cache is delivered, a CLI over a cron-synced store or a caching daemon over REST in the Evolution API style, is **open**; the cache store is the invariant both front doors read, so the choice can be sequenced rather than made once.

This settles that the accessor is majordomo's own and what data model it follows. It does not touch the separate open question in `DESIGN.md` of whether to fork and vendor `gchat-cli` as the pull accessor's starting code, which still rests on the pagination check recorded there.

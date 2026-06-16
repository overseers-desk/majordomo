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

**Information-repository (cache): an optional layer over the flow, owned by majordomo.** What is worth taking from mailroom is its contract, not its code: an external process maintains a local mirror, and the tool reads the mirror but falls back to the live source, with a provenance tag, when the mirror cannot serve a query. That contract is medium-neutral. Its implementation for chat — a local store of message JSON plus a sync job — must be built for chat specifically and belongs to majordomo, not to mailroom. Whether it is needed at all is unproven: reconstruction runs over a bounded date window, which a paginated live pull may serve well enough, and mailroom itself keeps its cache opt-in because only archive-scale repeated search justifies one. The cache is deferred until a real query volume shows it earns its place.

**Streaming is a use-case, not a foundation.** A live tail of incoming messages is wanted only by the downstream automation ambition, where an agent acts per message. The reconstruction-and-reporting core needs only a windowed pull, the same shape as a mail sync. The connector stays pull-based, like its siblings; streaming, if it comes, is a later mode and not a property of the accessor's data model.

## Decision

- majordomo is an **information-flow connector**: CLI primary, MCP secondary, JSON output, per-source, windowed pull. It is neither an object-edit tool nor a cache-first one.
- **crude is not the home.** Its object/CRUD model does not fit a read-mostly message flow whose valuable entity is reconstructed rather than stored. The crude-site candidate is rejected.
- **mailroom is the architectural template, not the host.** What carries across is the contract — two front doors, a sieve, identity-keyed accounts, and a provenance-tagged local-cache-with-live-fallback — not a shared cache, which is email-specific.
- A local cache is an **optional, deferred** layer majordomo may add later by copying mailroom's contract; build it only once windowed reconstruction proves too slow against the live API.
- Reconstruction, assignee and space reporting, the sieve, and the MCP front door sit **above the flow** and stay medium-agnostic.

This settles that the accessor is majordomo's own and what data model it follows. It does not touch the separate open question in `DESIGN.md` of whether to fork and vendor `gchat-cli` as the pull accessor's starting code, which still rests on the pagination check recorded there.

# WORLD_AS_OF: design for replay-bounded reads

WORLD_AS_OF is an office-wide environment variable holding an ISO-8601 timestamp with timezone (e.g. `2026-07-12T17:07:00+10:00`). When set, nothing dated after that instant may leave majordomo, so a benchmark session replayed later sees the world as it stood then. Unset means normal operation at zero cost. Set but unparseable is a hard failure on every code path, including commands that take no dates, because a silently ignored bound produces a contaminated run that looks valid. This document is the implementation design; the commits that follow it carry it out (the config parser through the documentation commit), and it changed no code itself.

## 1. The data-fetch surfaces

majordomo exposes four report surfaces, each reachable through two front doors (the CLI commands in `cli.py` and the same-named MCP tools in `mcp_server.py`). Both front doors call the same reader seam (`readers.make_reader`), and a reader is one of three backends: `CacheReader` (MySQL mirror), `NocacheReader` (direct Chat API), or `FreshReader` (`--live`, cache plus API top-up). This seam is why the change is tractable: one enforcement layer covers both front doors and all three backends.

| Surface | Backing store | Append-only or mutable |
|---|---|---|
| `messages` | `googlechat_messages` (cache) / `spaces.messages.list` (API) | Append-only by `create_time`, with two mutable edges: a message edited after the cutoff carries its post-edit text (the mirror keeps verbatim current JSON; the API returns current text), and a message deleted after the cutoff is gone from the API and, if the syncer removes it, from the mirror. |
| `tasks` | `coord_tasks` (cache) / decoded from messages (API) | Append-only. A task row is derived from its creation message and keyed by `created_at`; `status` is constantly `"open"` (Chat carries no reliable completion), so there is no mutating field in practice. |
| `spaces` | `googlechat_spaces` (cache) / `spaces.list` (API) | Mutable current-state. Display names change in place, membership changes, and spaces created after the cutoff appear in the list. The mirror keeps no history of prior names. The per-space message and task counts, however, are computed from the append-only tables and can be bounded exactly. |
| `people` | Aggregate over `googlechat_messages` union `coord_tasks` (cache) / same aggregation over API messages | The counts are append-only derived and boundable exactly. The display names are exact-at-creation on both backends: cache-side the prose `@name` frozen in `coord_tasks.assignee_display` (message senders carry no display name), API-side the same prose `@name` from the message text. The mutable, current-state `googlechat_users.display_name` is joined only by the `tasks` surface, not by `people`, so `people` has no rewind gap. |

`login` and `install-claude-command` fetch no chat data and are out of scope. `mcp` is a front door, covered by the seam.

## 2. How the bound applies, per surface and backend

The single fact that makes this clean: every dated read already flows through an end-exclusive upper bound. Cache SQL uses `create_time < %s` / `created_at < %s` (`reports.py`), and the API filter uses `createTime < "..."` (`api._time_filter`). So the bound is "clamp `end`": `end = min(end or WORLD_AS_OF, WORLD_AS_OF)` wherever an end bound exists, converting the timestamp to naive UTC first, since the store and `dates.resolve` work in naive UTC. A record stamped exactly at the instant is excluded by `<`; if inclusive semantics are wanted, the clamp value is the instant plus one second, a choice to record in one place.

- **`messages`, cache**: server-side query filter, exact. The clamp lands in the existing `create_time < %s` clause.
- **`messages`, API**: server-side query filter, exact for existence, via `createTime < ...`. Text content is post-cutoff-current for edited messages (see boundary rule 3).
- **`tasks`, cache**: server-side query filter on `created_at`, exact.
- **`tasks`, API**: the message fetch is server-bounded by `createTime`, and the decoder derives `created_at` from the creation message, so the result is exact without post-filtering.
- **`people`, both backends**: the aggregation windows are already `start`/`end` parameters; clamping `end` bounds the counts exactly, server-side in cache, at the message fetch in the API path. Its display names are the exact-at-creation prose `@name` (`coord_tasks.assignee_display`), so nothing is rewound here; the once-per-run current-state note (§3 rule 1) is a run-level blanket, justified by the `spaces` and `tasks` surfaces.
- **`spaces`, cache**: partially enforceable server-side. The `messages` and `tasks` count subqueries in `reports.spaces` gain `create_time < bound` / `created_at < bound`, which also makes the `minimal_messages >= 1` default drop spaces whose first message postdates the cutoff, a good proxy for "the space did not yet visibly exist". Space metadata (display name, type) is current-state and flagged.
- **`spaces`, API**: `spaces.list` accepts no date filter, so this is post-filter territory. The Space resource carries `createTime` for spaces created after roughly mid-2021; drop spaces whose `createTime` postdates the bound, keep the rest as current-state and flag. Message/task counts are already `None` on this path, so no count leak exists.
- **`--live` (`FreshReader`)**: under a past-instant bound the top-up is definitionally useless, since it fetches records newer than the cache watermark and the watermark of a mirrored space almost always postdates the bound. The clean semantics: when WORLD_AS_OF is set, `_api_start` clamps and the top-up is skipped whenever `watermark >= bound`, which degrades `--live` to the cache read plus a stderr note. This preserves `--live` on the one legitimate case, a bound in the future or inside the sync gap, without special-casing the flag away.
- **Window resolution (`dates.resolve`)**: relative windows (`7d`, `30d`, `month`, `year`) anchor to `_now_utc()`. Under replay, "now" is the frozen instant, so `resolve` takes the bound as its clock: `7d` means the seven days before WORLD_AS_OF, and `month` means the calendar month before the one containing it. Without this, a replayed `--window 7d` resolves to an empty range and the run silently reports nothing, which is the contamination twin: an answer shaped by the replay date rather than the as-of date. A user-supplied `--until` later than the bound is clamped down, with a stderr note naming both values.
- **`send`**: refused outright while the bound is set. A bounded run is a replay; a send would act in the real present, not the replayed instant.

## 3. The honest boundary where exactness is impossible

Four places cannot be rewound, and the rule is to say so rather than pretend:

1. **Space and user metadata is current-state.** `googlechat_spaces.display_name`, `space_type`, and `googlechat_users.display_name` keep no history. Rule: serve current values and flag the output once per run (a stderr line for the CLI, a field in the MCP/JSON envelope, see below). The API-side `assignee` name decoded from message text is exempt: it was frozen at creation and is exact.
2. **Deletions cannot be restored.** A message deleted between the as-of instant and the replay is absent from the API and possibly from the mirror. Rule: accept the loss silently on the API path (nothing detects it) and note in documentation that the cache path is the higher-fidelity replay source, since the mirror retains rows the API has since dropped.
3. **Edits show post-cutoff text.** Neither the mirror nor the API keeps pre-edit bodies. The API's `lastUpdateTime` distinguishes edited messages; the mirror's verbatim JSON carries it too if mirrored after the edit. Rule: serve the current text; where `lastUpdateTime > bound > createTime` is observable, mark the row (`edited_after_bound: true`) rather than dropping it, because dropping would misreport the message as never sent, a worse lie than a newer wording.
4. **Cache completeness has a floor.** The mirror keeps a twelve-month floor; a bound older than the oldest cached message yields a silently thin answer. Rule: when `bound < MIN(create_time)` for the queried scope, emit a warning that the store does not reach the as-of instant. This is contamination by omission and deserves the same visibility as leakage.

## 4. The pre-filled-prompt data path

majordomo pre-serves exactly one artifact into AI context: the Claude Code command file that `_claude_command.refresh()` rewrites on every run (`~/.claude/commands/majordomo.md`). Its content is the static `COMMAND` string, instructions and example invocations only; it embeds no chat records, counts, or dates fetched from any store. There is therefore no data to bound on this path today. The design still touches it in one way: the command body gains a sentence documenting WORLD_AS_OF, so an agent operating under a replay harness knows the bound is honored and does not attempt its own filtering. Should a future version pre-compute rows into the command file or an MCP resource, that generation call goes through the same reader seam and inherits the bound for free; the invariant to keep is "no data reaches a prompt except through a Reader".

## 5. Feasibility verdict

Feasible and clean, because the codebase already funnels every read through one seam and already expresses every dated read as an end-exclusive upper bound. Effort: **S to M** (roughly one session): a `config.world_as_of()` parser, a clamp in `dates.resolve`, bound predicates in two `reports.spaces` subqueries, the `FreshReader` skip, the space `createTime` post-filter in `api.spaces`, the envelope/stderr flagging, and tests. The sharp edges:

- **Silent-fallback leak.** `make_reader`'s auto mode falls back from cache to the direct API when the DB is down. Both backends enforce the bound, so this is safe, but only if the bound is applied inside each backend rather than at the CLI layer; enforcement at the seam, not the front door, is the design's one structural commitment.
- **Timezone conversion.** The store is naive UTC; the variable carries an offset. The parser rejects a timestamp without an offset (hard failure, same as unparseable), converts to UTC, then drops tzinfo. Getting this wrong shifts the boundary by hours and is invisible in tests that use UTC inputs; the test set needs a non-UTC offset case.
- **Hard failure everywhere, including `spaces` and MCP.** The parse lives in `config.load_config()` (or a sibling called by it), which every command and every MCP tool call already passes through, so a bad value stops even date-free commands. The MCP server parses per tool call, not at startup, so a long-running server honors an environment set by its launcher and fails per-call with a clear message rather than dying opaquely at handshake.
- **The frozen-now decision.** Anchoring relative windows to the bound changes the meaning of `7d` under replay. This is the intended semantics but the largest behavioral choice in the design; it is settled here rather than left to the implementer.
- **Auditability.** The JSON/MCP envelope gains `"world_as_of": "<the bound>"` when set (absent otherwise), so a benchmark log proves each answer was bounded. Zero cost when unset.

## 6. Staging for a later session to implement

Commits in order, each independently green, guess-dependent work last:

1. **`config.world_as_of()`**: read the variable, parse with offset required, hard-fail (`SystemExit` with the offending value and the expected format) on anything else, return naive-UTC datetime or `None`. Wire into `load_config()`. Tests: unset, valid with offset, valid non-UTC offset, missing offset, garbage.
2. **`dates.resolve` clamp and frozen now**: thread the bound into `resolve` (clamp `end`, anchor windows), stderr note on a clamped `--until`. Tests per window.
3. **Backend enforcement**: bound predicates in `reports.spaces` subqueries; `NocacheReader` clamps in `_time_filter` callers and post-filters `spaces()` on Space `createTime`; `people`/`tasks`/`messages` inherit via the clamped `end`. Verify with the existing parity tests plus bound cases.
4. **`FreshReader` degradation**: skip top-up targets whose watermark is at or past the bound; stderr note that `--live` served cache under the bound.
5. **Flagging and envelope**: `world_as_of` in the JSON/MCP envelope, current-state metadata warning, cache-floor warning, `edited_after_bound` where observable.
6. **Documentation**: README, the `COMMAND` body in `_claude_command.py`, and `--help` epilogs.

Resume by reading this file; the enforcement-at-the-seam commitment (edge one under the verdict) is the only decision the implementer needs to hold throughout.

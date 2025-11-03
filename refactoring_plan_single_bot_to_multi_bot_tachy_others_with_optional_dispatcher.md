# Refactoring Plan: Single-Bot to Multi-Bot ("Tachy + others") with Optional Dispatcher

> **Scope**: Refactor the current single bot into a multi-bot system where the current bot is renamed **Tachy** and becomes one of several bots. Introduce an **optional** Dispatcher for orchestration while preserving a vendor-native **Direct mode** for simple installations. No source code proposed here; this document covers architecture decisions, rationale, contracts, rollout, and operations.

---

## 1) Goals & Non‑Goals

**Goals**

- Support multiple bots with different costs, latencies, and permissions.
- Keep vendor-native path viable for small/simple deployments.
- Prevent duplicate processing and race conditions seen with multiple callbacks.
- Centralize policy (routing, permissions, redaction) **when needed** without mandating it.
- Improve observability (structured logs, tracing), reliability (idempotency, retries), and security (secrets isolation).

**Non-Goals**

- No application code in this document.
- No vendor lock-in beyond current Google Chat footprint.
- Not redesigning user-facing UX flows; focus is backend/event architecture.

---

## 2) Current State (Problem Summary)

- Single bot does everything (to be renamed **Tachy**).
- Google Chat triggers callbacks on messages/mentions; there is a risk of **duplicate triggers** if multiple apps subscribe.
- Secrets are co-located; logging is not standardized; routing/permission logic is embedded and not reusable.

**Impacts today**: tight coupling, higher risk of double-processing, limited ability to add more bots safely, and difficulty enforcing policy/redaction consistently.

---

## 3) Target Architecture (Two Deployment Modes)

### 3.1 Direct Mode (Vendor-Native, Quickstart)

- Google Chat → **Bot’s public callback** → Bot handles business logic directly.
- Minimal setup; recommended for **single-room/single-bot** use cases.
- Policies are implemented within the bot (e.g., Tachy-only), with a **SingleBotPolicy** mindset.

### 3.2 Orchestrated Mode (Dispatcher-Centric, Advanced)

- Google Chat → Event subscription / PubSub → **Dispatcher** → Internal **/invoke** for each bot.
- Bots do **not** process public callbacks; if platform forces callbacks, bots forward to the event bus and return immediately (no business logic in public endpoints).
- Dispatcher applies routing (cost/latency/permissions), redaction gates, and optional verification/escalation.

> **Decision**: The Dispatcher is **optional** and acts as **progressive enhancement**. Teams can start in Direct Mode and later enable Orchestrated Mode without rewriting business logic.

---

## 4) Key Components & Responsibilities

- **Tachy (existing bot)**: Renamed current bot; implements its domain-specific skills. In Orchestrated Mode, exposes internal **/invoke** and emits results/telemetry.
- **Other Bots**: Additional specialized bots (e.g., knowledge, summarization, compliance) following the same contracts as Tachy.
- **Dispatcher (optional)**: Stateless control plane that normalizes events, evaluates policy, routes to bots, manages deduplication/idempotency, and applies redaction/permission checks.
- **Event Bus (Pub/Sub)**: Transports normalized events (e.g., `ChatMessageReceived`, `BotInvocationRequested`, `BotResult`).
- **Responder**: Sends bot results back to Google Chat; enforces edits/recalls only via redaction policy.
- **Redaction/Compliance**: Independent gate that can redact or recall messages based on policy; auditable.
- **Observability Stack**: Centralized logs, metrics, tracing; correlation by `trace_id` and `conversation_id`.
- **Secrets Manager**: Per-bot secrets and shared secrets referenced by name; no secrets in code or flat JSON files.

---

## 5) Architecture Decisions (ADR-style)

**ADR‑001 – Multi-Bot Architecture; rename current bot to Tachy**

- **Decision**: Treat the current bot as **Tachy** within a multi-bot ecosystem.
- **Rationale**: Enables specialization, independent lifecycles, and policy-based routing.

**ADR‑002 – Dispatcher is Optional (Two Modes)**

- **Decision**: Support **Direct Mode** (no Dispatcher) and **Orchestrated Mode** (with Dispatcher). Configure via environment/flag.
- **Rationale**: Aligns with Google’s native model for simple installs; unlocks orchestration for complex deployments.

**ADR‑003 – Single Ingress Per Mode**

- **Decision**: In Orchestrated Mode, **only** Dispatcher processes chat events. If the platform forces public bot callbacks, those callbacks forward to the event bus and **do not** execute business logic.
- **Rationale**: Eliminates duplicate triggers and ordering races.

**ADR‑004 – Normalized Event Contracts (no code)**

- **Decision**: Define vendor-agnostic event shapes for: `ChatMessageReceived`, `BotInvocationRequested`, `BotResult`, `RedactionDecision`.
- **Rationale**: Decouples Dispatcher/bots from Google-specific payloads and stabilizes integrations.

**ADR‑005 – Policy Engine**

- **Decision**: Dispatcher uses a policy layer for routing (cost, latency, permissions, confidence thresholds, escalation).
- **Rationale**: Centralizes decisions; avoids duplicating rules across bots.

**ADR‑006 – Idempotency & Deduplication**

- **Decision**: Deduplicate by immutable `message_id` and guard with idempotency keys in Dispatcher and Responder; maintain a processed-store with TTL.
- **Rationale**: Prevents duplicate processing from multiple ingress paths/retries.

**ADR‑007 – Observability**

- **Decision**: Structured JSON logs with `trace_id`/`conversation_id`; centralized metrics/dashboards; error budgets and SLOs per hop.
- **Rationale**: Debuggability, compliance, and performance management.

**ADR‑008 – Secrets & Config**

- **Decision**: Secrets in a secrets manager; per-bot config overlays; shared secrets referenced (not duplicated).
- **Rationale**: Principle of least privilege; safer rotation and auditing.

**ADR‑009 – Compliance & Redaction**

- **Decision**: Redaction decisions are made by a dedicated step (service/middleware); bots themselves do not edit/recall messages directly.
- **Rationale**: Auditability and reduced risk of accidental disclosure.

**ADR‑010 – Install & Ops Ergonomics**

- **Decision**: Provide two setup guides: **Quickstart (Direct Mode)** and **Advanced (Orchestrated Mode)**; optional automation scripts/IaC to bootstrap Pub/Sub, IAM, and subscriptions.
- **Rationale**: Keep time-to-value low while enabling advanced deployments.

---

## 6) Event & Interaction Flows (Informal)

**Direct Mode (Tachy-only or few bots without Dispatcher)**

1. Google Chat posts callback → Tachy public endpoint.
2. Ingress adapter normalizes payload → `ChatMessageReceived` (in-memory).
3. Policy (SingleBotPolicy) → invoke Tachy.
4. Tachy produces result → Responder posts to Google Chat.
5. Logs/metrics/traces emitted with `trace_id`.

**Orchestrated Mode (multi-bot with Dispatcher)**

1. Google Chat event → Event Adapter → Pub/Sub topic `chat-events`.
2. Dispatcher consumes → dedupe/idempotency check.
3. Policy evaluation (permissions, cost/latency, escalation rules).
4. Dispatcher emits `BotInvocationRequested` → target bot internal endpoint.
5. Bot runs, emits `BotResult` (status/cost/confidence/metadata).
6. Optional verifier/redaction; then Responder posts to Chat.
7. All steps emit structured telemetry with the same `trace_id`.

> If platform-mandated bot callbacks exist, those callbacks forward events to `chat-events` and return immediately.

---

## 7) Informal Data Contracts (No Code)

**ChatMessageReceived (normalized)**

- Identifiers: message\_id (immutable), space\_id, thread\_id, sender\_id, timestamp
- Content: text, attachments, mentions[]
- Context: tenant/org, channel\_type (room/DM), pii\_flags (if already detected)
- Meta: vendor\_payload\_ref, trace\_id

**BotInvocationRequested**

- Target: bot\_name, reason (mention/routing/escalation)
- Inputs: message\_ref (message\_id), normalized content/context
- Policy: requested\_capabilities[], permission\_scope
- Meta: trace\_id, idempotency\_key

**BotResult**

- Outcome: status (COMPLETED, PARTIAL, ESCALATE, ERROR), confidence
- Response: text, actions (optional), artifacts (refs), cost\_estimate, latency\_ms
- Meta: message\_ref, bot\_name, trace\_id

**RedactionDecision**

- Action: NONE, MASK, DELETE, REPOST
- Reason: PII, policy\_violation, user\_request, legal\_hold
- Meta: acted\_message\_ref, approver/policy\_ref, trace\_id

---

## 8) Security & Permissions

- **Least privilege** IAM: Dispatcher can read events and call bots; each bot has scoped access only to its required APIs.
- **Secrets isolation** per bot; shared secrets referenced via names.
- **Data minimization**: pass only necessary fields to each bot.
- **Audit trails**: immutable logs for redaction/recall with before/after hashes.

---

## 9) Reliability, Scale & SLOs (Targets)

- **SLO (p95)**: Ingress→Dispatch ≤ 200 ms; Bot execution budget defined per bot (e.g., cheap: ≤ 800 ms; expensive: ≤ 5 s).
- **At-least-once** processing with idempotency on `message_id`.
- **Retries** with exponential backoff; **DLQ** for poison messages.
- **Partitioning** by `space_id` to preserve ordering where required.
- **Circuit breakers** for slow/expensive bots; fallback pathways.

---

## 10) Installation & Operations

**Quickstart (Direct Mode)**

- Register the bot (Tachy) in Google Chat.
- Configure public callback URL.
- Provide minimal config/secrets.
- Verify end-to-end via a smoke checklist.

**Advanced (Orchestrated Mode)**

- Create Pub/Sub topic and subscriptions.
- Grant IAM for Dispatcher and bots.
- Configure event subscription from target rooms/spaces.
- Deploy Dispatcher and bots; point each bot’s public callback (if required) to forward into the topic.
- Validate with canary space; enable policy routing.

> Ship two documents: `` and ``. Provide optional automation (script/Terraform) to reduce setup time.

---

## 11) Migration Plan (Single Bot → Multi-Bot)

**Phase 0 – Preparation**

- Rename current bot to **Tachy** in docs/config/registries.
- Introduce normalized event models (internal types only, no code published here).
- Stand up centralized logging/trace IDs.

**Phase 1 – Dual Ingress (no behavior change)**

- Add an ingress adapter around Tachy’s callback to emit normalized events internally.
- Add idempotency keying on `message_id`.

**Phase 2 – Dispatcher Shadow (optional)**

- Deploy Dispatcher in **shadow mode**: consumes events, runs policy, but does not post to Chat; compare decisions vs Tachy’s direct handling.

**Phase 3 – Controlled Orchestration**

- Enable Orchestrated Mode for one space; Tachy invoked via Dispatcher.
- Introduce a second bot and routing policy (e.g., cheap-first escalation).

**Phase 4 – Expand & Harden**

- Roll out to more spaces; enable redaction/verification gates where required.
- Tune SLOs, retries, and circuit breakers.

**Phase 5 – Optional Decommission of Direct Path**

- If stable, turn public bot callbacks into forward-only (or disable) for orchestrated spaces.

**Rollback Plan**

- Switch flag to Direct Mode; Dispatcher drains and stops consuming; public callbacks resume full handling.

---

## 12) Documentation & Naming

- Use **Tachy** consistently for the existing bot.
- Provide separate guides: **Bot Setup** (per-bot) vs **Dispatcher Setup** (platform/IAM/events).
- Maintain a **Bot Registry** (YAML/JSON, not code here) containing: name, capabilities, required scopes, internal endpoint, cost tier, latency class.

---

## 13) Testing Strategy (No Code)

- **Contract tests** for event normalization and routing decisions.
- **E2E tests** per deployment mode.
- **Chaos drills**: drop/delay events, spike latency, DLQ handling.
- **Policy tests**: permission denies, escalation thresholds, redaction triggers.
- **Performance tests**: steady-load and burst across rooms.

---

## 14) Risks & Mitigations

- **Setup complexity (Orchestrated Mode)** → Provide Quickstart; automation scripts; keep Dispatcher optional.
- **Single point of coordination** → Stateless Dispatcher, horizontal scale, health checks, partitioning, DLQ.
- **Vendor behavior changes** → Maintain normalized contracts; keep Direct Mode viable.
- **Privacy/PII leakage** → Redaction gate, least-privilege secrets, audit logs.
- **Cost blowups** → Policy limits, circuit breakers, budget alarms per bot.

---

## 15) Open Questions

- What initial set of additional bots will ship alongside Tachy (names/capabilities)?
- Which verification model (if any) is used for low-confidence results?
- Per-room vs tenant-wide policies—who owns the policy source of truth?
- What is the default redaction action for suspected PII (mask vs recall)?
- Do we require ordered processing per space/thread or best-effort?

---

## 16) Acceptance Criteria

- Tachy continues to function in **Direct Mode** with no regressions.
- Orchestrated Mode can be enabled per space/tenant via configuration.
- Duplicate processing is prevented (idempotency proved in tests).
- Centralized logs/metrics/traces visible for both modes.
- Documentation split: **Quickstart (Direct)** and **Advanced (Orchestrated)** published.

---

## 17) Appendix: Text Diagram (for orientation)

```
Direct Mode:
Google Chat → Tachy (public callback) → Responder → Google Chat
                 │
                 └─ Logs/Metrics/Trace

Orchestrated Mode:
Google Chat → Event Adapter → Pub/Sub: chat-events → Dispatcher
                                          │
                   ┌─────────── BotInvocationRequested ────────────┐
                   ▼                                                ▼
                Tachy (/invoke)                                 Other Bots
                   │                                                │
                   └───────────── BotResult ────────────────────────┘
                                         │
                                      Responder → Google Chat
```

---

**Bottom Line**: Rename the current bot to **Tachy**, keep **Direct Mode** for fast vendor-native installs, and add an **optional Dispatcher** for multi-bot orchestration when policy/routing/redaction and scale require it. This preserves simplicity while enabling growth without locking the system into a single approach.


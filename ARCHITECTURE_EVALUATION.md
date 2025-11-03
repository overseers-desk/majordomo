# Architectural Evaluation: Webhook vs Bot Integration for Google Chat

## Executive Summary

This document evaluates two architectural approaches for integrating automated responses into Google Chat: a **bot-centric model** and a **dispatcher-centric model**. The evaluation is based on Google Chat's platform capabilities, cost considerations, security requirements, separation of concerns, and operational efficiency.

**Key Finding**: Google Chat's platform constraints fundamentally limit architectural options. **Only Chat Apps (bots) can receive message events from chat spaces**. Incoming webhooks can only post messages; they cannot receive events. This technical limitation shapes all architectural decisions.

---

## Objectives

The architecture aims to support two primary activation paths:

1. **User-initiated activation**: The bot responds when explicitly mentioned in a chat (e.g., `@taskbot please handle this`).

2. **Dispatcher-based activation**: A dispatcher monitors chat messages or external system events and decides when to engage the bot proactively.

The second path enables context-sensitive responses without requiring users to explicitly mention the bot, as well as integration with external systems (e.g. booking systems, IoT sensors).

---

## Platform Capabilities and Constraints

### Google Chat Message Reception

According to Google's official documentation and implementation experience:

#### Chat Apps (Bots)

- **Can receive message events** when added to a space
- **Event types received**:
  - **Default behaviour**: Bots receive MESSAGE events only when @mentioned in a space
  - **In DMs (direct messages)**: Bots receive all messages sent by the user
  - **In spaces**: Bots receive events when explicitly @mentioned or when specific interactive features are triggered (slash commands, card interactions, etc.)

- **Configuration options** (Google Cloud Console > APIs & Services > Chat API > Configuration):
  - ✅ **"Receive 1:1 messages"**: Users can send direct messages to the app
  - ✅ **"Join spaces and group conversations"**: The app can be added to spaces and group conversations
  - ❌ **No setting exists** for "receive all messages in spaces without @mention"

- **Scopes typically required**:
  - `https://www.googleapis.com/auth/chat.messages` (send messages)
  - `https://www.googleapis.com/auth/chat.messages.readonly` (read messages)
  - `https://www.googleapis.com/auth/chat.spaces` (access spaces)

- **Implementation mechanism**: The bot exposes an HTTP endpoint that Google Chat calls with event payloads

**Critical limitation**: Google Chat does **not** provide a configuration option for bots to passively receive all messages in a space without being @mentioned. This is a fundamental platform constraint.

#### Incoming Webhooks

- **Can only post messages** to a specific chat space
- **Cannot receive any events** from Google Chat
- **No authentication mechanism** beyond knowing the webhook URL
- **Use case**: Simple, one-way notifications (e.g. CI/CD pipeline posting build results)

#### Critical Constraint

**There is no mechanism for Google Chat to call a webhook when a message is posted.** Only Chat Apps can receive message events. This fundamentally rules out "Option B: Webhook as Unified Dispatcher" for chat message handling.

---

## Architectural Options

Given Google Chat's platform capabilities, there are effectively three architectural patterns for building a comprehensive chat integration:

### Option A: Bot-Centric Model (Mention-Only)

```
External Systems ──→ Custom Webhook ──→ Bot Logic
                                          ↑
Google Chat @Mentions ────────────────────┘
```

**Architecture**:

- The bot receives message events only when @mentioned in a space
- External systems send triggers to a separate webhook endpoint
- The webhook forwards external triggers to the bot logic
- All processing logic resides in the bot

**Best for**: Simple conversational bots where proactive monitoring isn't needed.

### Option B: Events API Model (Monitor All Messages)

```
External Systems ──→ Custom Webhook ──→ Bot Logic
                                          ↑
Google Chat Messages ──→ Workspace Events API ──→ Cloud Pub/Sub ──→ Event Handler ──→ Bot Logic
```

**Architecture**:

- Subscribe to all message events via Google Workspace Events API
- Events delivered to Cloud Pub/Sub topic in real-time
- Event handler processes Pub/Sub messages
- Bot logic invoked when needed (with filtering in event handler)
- External systems communicate via separate webhook

**Best for**: Bots that need to monitor all messages proactively (alerts, compliance, task tracking).

### Option C: Hybrid Dispatcher Model

```
External Systems ──→ Dispatcher Webhook ──→ Bot Logic (when needed)
                                          ↓
                                    Google Chat (via incoming webhook)

Google Chat @Mentions ──→ Bot Endpoint ──→ Dispatcher Logic ──→ Bot Logic (when needed)

Google Chat Messages ──→ Workspace Events API ──→ Pub/Sub ──→ Dispatcher Logic ──→ Bot Logic (when needed)
```

**Architecture**:

- Combines @mention handling (Option A) with Events API monitoring (Option B)
- Central dispatcher routes all events (mentions, all messages, external triggers)
- Dispatcher applies filtering logic before engaging heavy bot processing
- Can respond via direct API calls or incoming webhook depending on context

**Best for**: Complex systems with multiple trigger types and sophisticated filtering requirements.

---

## Previous Architectural Variants (For Context)

The following sections describe architectural variants that were considered before the discovery of the Google Workspace Events API. These are preserved for understanding the evolution of the architecture.

---

## Comparative Analysis

### 1. Platform Compliance

| Criterion | Option A: Bot-Centric (Mention-Only) | Option B: Events API (All Messages) | Option C: Hybrid Dispatcher |
|-----------|--------------------------------------|-------------------------------------|----------------------------|
| **Complies with platform constraints** | ✅ Yes | ✅ Yes | ✅ Yes |
| **Chat message reception** | Bot endpoint (mentions only) | Workspace Events API + Pub/Sub | Both methods combined |
| **Receives all messages** | ❌ No (mentions only) | ✅ Yes (via Events API) | ✅ Yes (via Events API) |
| **External triggers** | Custom webhook | Custom webhook | Unified dispatcher webhook |
| **Infrastructure complexity** | Low | Medium (adds Pub/Sub) | High (combines all approaches) |

**Analysis**: All options comply with Google's platform. Option B introduces the Workspace Events API as the modern solution for monitoring all messages. Option C combines multiple approaches for maximum flexibility at the cost of complexity.

### 2. Message Filtering Efficiency

| Criterion | Option A: Bot-Centric (Mention-Only) | Option B: Events API (All Messages) | Option C: Hybrid Dispatcher |
|-----------|--------------------------------------|-------------------------------------|----------------------------|
| **Filtering location** | Inside bot logic | In event handler (before bot) | Central dispatcher |
| **Processing overhead** | Low (only processes mentions) | Medium (processes all, but can filter) | Medium-High (routes all events) |
| **Response time** | Fast (direct push events) | Fast (real-time Pub/Sub) | Fast (multiple real-time paths) |
| **Messages processed** | Only @mentions | All messages | All messages + mentions |
| **Filtering flexibility** | Limited (mentions only) | High (can apply any filter logic) | Highest (unified filtering) |

**Analysis**: 

- **Option A** is most efficient per-message (only processes mentions) but lacks proactive monitoring capability
- **Option B** receives all messages but allows filtering in the event handler before engaging expensive bot logic
- **Option C** provides maximum flexibility with unified filtering across all event sources

**Real-world impact**: For high-volume channels, Options B and C require efficient filtering:

- Filter at event handler level (lightweight text matching, pattern recognition)
- Only invoke bot logic for relevant messages (database queries, LLM inference)
- Example: 1,000 messages/day → filter to 50 relevant → only 50 expensive operations

### 3. External System Integration

| Criterion | Option A: Bot-Centric (Mention-Only) | Option B: Events API (All Messages) | Option C: Hybrid Dispatcher |
|-----------|--------------------------------------|-------------------------------------|----------------------------|
| **Integration point** | Single webhook forwards to bot | Single webhook forwards to bot | Unified dispatcher webhook |
| **Simple notifications** | Via incoming webhook or bot | Via incoming webhook or bot | Dispatcher routes appropriately |
| **Complex notifications** | Bot logic handles | Bot logic handles | Dispatcher decides routing |
| **External system changes** | May require bot code changes | May require bot code changes | Can handle via dispatcher config |

**Analysis**: Option C provides the **best separation of concerns** for external integrations. The unified dispatcher can:

- Post simple notifications directly (without engaging bot logic)
- Route complex scenarios to bot logic
- Apply business rules centrally (e.g., "only notify if severity > 5")
- Maintain consistent security policies across all entry points

Options A and B keep external integration separate from chat handling, which is simpler but less flexible.

### 4. Security Considerations

| Criterion | Option A: Bot-Centric (Mention-Only) | Option B: Events API (All Messages) | Option C: Hybrid Dispatcher |
|-----------|--------------------------------------|-------------------------------------|----------------------------|
| **Authentication points** | Bot (Google) + Webhook (external) | Bot + Webhook + Pub/Sub | Bot + Dispatcher (all sources) |
| **External system access** | Webhook validates external systems | Webhook validates external systems | Dispatcher centralises validation |
| **Attack surface** | Two endpoints | Three components (bot, webhook, Pub/Sub) | Three components but unified security |
| **Credential management** | Bot needs Chat API credentials | Bot + Events API credentials | Unified credential management |
| **Event authenticity** | Google verifies bot requests | Google + Pub/Sub message verification | Centralized verification |

**Analysis**: Security considerations vary by complexity:

- **Option A**: Simplest security model (two endpoints to protect)
- **Option B**: Adds Pub/Sub security layer (message authenticity, subscription authorization)
- **Option C**: Most complex but can centralize security policies for consistency

**Best practices for all options**:

- Implement request signing (HMAC-SHA256) for external webhooks
- Use TLS/HTTPS for all endpoints
- Validate Google Chat requests using bearer tokens
- For Pub/Sub: Verify message authenticity using Pub/Sub's built-in authentication
- Apply rate limiting to prevent abuse
- Log all access attempts for security monitoring

### 5. Cost Considerations

| Criterion | Option A: Bot-Centric (Mention-Only) | Option B: Events API (All Messages) | Option C: Hybrid Dispatcher |
|-----------|--------------------------------------|-------------------------------------|----------------------------|
| **Google Chat API costs** | **FREE** | **FREE** | **FREE** |
| **Events API costs** | N/A | **FREE** | **FREE** |
| **Cloud Pub/Sub costs** | N/A | Minimal (likely FREE tier) | Minimal (likely FREE tier) |
| **Infrastructure costs** | One bot service + webhook | Bot + webhook + Pub/Sub subscriber | All components combined |
| **Processing costs** | Low (only mentions processed) | Variable (filter before processing) | Variable (unified filtering) |
| **Development costs** | Simplest | Moderate complexity | Highest complexity |
| **Maintenance costs** | Single codebase | Multiple components | Most components to maintain |

**Google Workspace API Pricing** (as of 2024):

- **Google Chat API**: FREE for all Workspace accounts
- **Workspace Events API**: FREE for all Workspace accounts
- **No quota costs** for receiving events or sending messages
- **Cloud Pub/Sub**: [Pricing tiers](https://cloud.google.com/pubsub/pricing)
  - First 10 GB/month: **FREE**
  - Typical chat message volume easily within free tier
  - Example: 10,000 messages/day × 365 days × 2 KB/message = ~7.3 GB/year (**FREE**)

**Real cost difference**: Costs come from **processing infrastructure**, not Google APIs:

- **Option A**: Lowest infrastructure costs (simple bot + webhook)
- **Option B**: Adds Pub/Sub subscriber service, but Pub/Sub itself likely free
- **Option C**: Highest infrastructure but can optimize processing efficiency

**Example scenario**: High-volume chat space with 1,000 messages/day:

- **Option A**: Processes only @mentions (e.g., 20/day)
  - Very low processing costs
  - Cannot implement proactive monitoring

- **Option B**: Receives all 1,000 messages, filters to relevant 50
  - Event handler: 1,000 lightweight operations/day (cheap)
  - Bot logic: 50 expensive operations/day
  - LLM costs reduced by 95% compared to processing all messages

- **Option C**: Same as Option B plus @mention handling
  - Slightly higher complexity
  - Maximum flexibility for future requirements

**Conclusion**: For bots with expensive processing logic (database queries, LLM inference), **Option B provides significant cost savings** while enabling proactive monitoring. Option A is cheapest if proactive monitoring isn't needed.

### 6. Separation of Concerns

| Criterion | Option A: Bot-Centric (Mention-Only) | Option B: Events API (All Messages) | Option C: Hybrid Dispatcher |
|-----------|--------------------------------------|-------------------------------------|----------------------------|
| **Filtering logic** | In bot | In event handler (separate from bot) | In central dispatcher |
| **External integrations** | Separate webhook | Separate webhook | Unified dispatcher |
| **Message routing** | Implicit (mentions only) | Explicit filtering in handler | Centralized routing |
| **Testability** | Test bot for all scenarios | Test handler and bot independently | Test dispatcher independently |
| **Maintainability** | All bot logic changes together | Event handling separate from bot | Clear separation of all concerns |

**Analysis**: 

- **Option A**: Simplest - all logic in one place, but limited to mention-only responses
- **Option B**: Good separation - event handling + filtering separate from bot processing logic
- **Option C**: Best separation - unified dispatcher routes all events (chat mentions, all messages, external triggers)

**Benefits of separation** (Options B and C):

- **Independent testing**: Test event handling separately from bot logic
- **Independent scaling**: Deploy components on different infrastructure if needed
- **Team division**: Different teams can own different components
- **Easier updates**: Changing filtering rules doesn't require bot code changes

### 7. Scalability and Performance

| Criterion | Option A: Bot-Centric (Mention-Only) | Option B: Events API (All Messages) | Option C: Hybrid Dispatcher |
|-----------|--------------------------------------|-------------------------------------|----------------------------|
| **Horizontal scaling** | Scale bot service | Scale bot + event handler independently | Scale all components independently |
| **Vertical scaling** | Minimal resources needed | Event handler lightweight, bot scales as needed | Flexible scaling per component |
| **Bottleneck location** | Bot processing (minimal load) | Event handler filtering | Dispatcher routing |
| **Failure isolation** | Bot failure affects all | Event handler vs bot failures isolated | Better failure isolation |
| **Message throughput** | Low (mentions only) | High (all messages) | Highest (all event types) |

**Analysis**: 

- **Option A**: Best performance per-message but limited functionality
- **Option B**: Pub/Sub provides excellent scalability for high message volumes
- **Option C**: Most scalable but highest complexity

**Example scaling scenario**: During a high-traffic period (1,000+ messages/hour):

- **Option A**: Minimal load (only processes mentions)
- **Option B**: Event handler on lightweight infrastructure handles filtering; bot scales only for actual processing needs
- **Option C**: Dispatcher routes efficiently; each component scales independently

---

## Implementation Considerations

### Implementing Option A: Bot-Centric

**Components**:

1. **Chat App Bot Endpoint** (`google_chat_app.cgi`)
   - Receives all message events from Google Chat
   - Contains full processing logic
   - Sends responses via Chat API

2. **External Webhook Endpoint** (new)
   - Receives triggers from external systems
   - Validates and authenticates requests
   - Forwards to bot logic or posts via incoming webhook

**Advantages**:

- ✅ Simpler architecture with fewer moving parts
- ✅ Easier to understand and debug
- ✅ Single deployment unit
- ✅ Suitable for low-to-medium volume use cases

**Disadvantages**:

- ❌ All messages trigger full processing logic
- ❌ Higher resource consumption
- ❌ External integrations mixed with bot code
- ❌ Changes to any component require full redeployment

**Best suited for**:

- Small teams with simple bots
- Low message volumes (< 100 messages/day)
- Bots with simple, fast processing logic
- Projects prioritising simplicity over scalability

### Implementing Option B: Dispatcher-Centric

**Components**:

1. **Chat App Bot Endpoint with Dispatcher Logic** (`google_chat_app.cgi`)
   - Receives all message events from Google Chat
   - Contains lightweight filtering/routing logic
   - Forwards relevant messages to bot processor
   - Handles simple cases directly

2. **Bot Processor Service** (new or refactored)
   - Contains heavy processing logic
   - Called by dispatcher when needed
   - Sends responses via Chat API

3. **Dispatcher Webhook for External Systems** (new)
   - Receives triggers from external systems
   - Validates and authenticates requests
   - Posts simple notifications directly via incoming webhook
   - Routes complex scenarios to bot processor

**Advantages**:

- ✅ Efficient filtering before expensive processing
- ✅ Clear separation of concerns
- ✅ Independent scaling of components
- ✅ Better testability and maintainability
- ✅ Reduced costs for high-volume scenarios

**Disadvantages**:

- ❌ More complex architecture
- ❌ Additional deployment components
- ❌ Potential for over-engineering in simple cases
- ❌ Requires careful design of dispatcher rules

**Best suited for**:

- Medium to large teams with complex requirements
- High message volumes (> 100 messages/day)
- Bots with expensive processing (database, LLM, external APIs)
- Projects requiring clear separation of concerns
- Systems integrating multiple external triggers

### Code Organisation Example: Option B

```
/chatbot/
  dispatcher.py          # Lightweight filtering and routing logic
  processor.py           # Heavy bot processing logic
  external_webhook.py    # External system integration
  rules.py              # Filtering rules configuration

/google_chat_app.cgi     # Entry point: calls dispatcher.handle_event()
```

**Dispatcher logic** (`dispatcher.py`):

```python
def handle_chat_message(event):
    # Lightweight checks
    if should_ignore_message(event):
        return {}  # No action
    
    if is_simple_command(event):
        return handle_simple_command(event)  # Quick response
    
    # Complex case: invoke full bot logic
    return processor.process_message(event)

def should_ignore_message(event):
    # Quick filtering: check sender, keywords, message type
    # No database queries, no API calls
    if event['sender']['email'].endswith('@example.com'):
        return True
    
    text = event['message']['text'].lower()
    if not any(keyword in text for keyword in ['task', 'help', 'status']):
        return True
    
    return False
```

This keeps the dispatcher lightweight whilst delegating heavy processing to the `processor` module.

---

## Google Chat Bot Configuration: The "Receive All Messages" Myth

### Current Behaviour

The bot in this repository receives MESSAGE events only when it is @mentioned in a space. This is the **standard and only behaviour** for Google Chat bots in spaces.

### Can Bots Receive All Messages Without @Mentions?

**Short answer: No, not via push events.**

After examining the actual Google Cloud Console configuration page, the following is confirmed:

#### Available Configuration Options

Navigate to: **Google Cloud Console > APIs & Services > Chat API > Configuration**

The **"Functionality"** section has only two checkboxes:

1. ✅ **Receive 1:1 messages**: Users can send direct messages to the app
2. ✅ **Join spaces and group conversations**: The app can be added to spaces

**There is no third option** for "receive all messages in spaces" or "receive messages without @mention".

#### What This Means

- **In DMs (1:1 conversations)**: The bot receives all messages sent by the user
- **In spaces**: The bot receives MESSAGE events **only when @mentioned**
- **No configuration exists** to change this behaviour

#### Why the Confusion?

Many developers (and AI assistants!) assume this feature exists because:

1. It would be useful for many use cases
2. Other chat platforms (Slack, Discord) offer this option
3. Google's documentation mentions bots "listening" to spaces, which sounds like passive monitoring
4. The OAuth scope `chat.messages.readonly` sounds like it should enable reading all messages

However, **none of these assumptions are correct**. The configuration option simply doesn't exist in the Google Cloud Console.

### The Reality: How to Monitor All Messages

If you need to monitor all messages in a space (not just mentions), you have three options:

#### Option 1: Real-Time Subscriptions via Google Workspace Events API (Recommended)

**This is the modern, recommended approach** for receiving all messages in real-time.

Use the [Google Workspace Events API](https://developers.google.com/workspace/events) to subscribe to chat events via Cloud Pub/Sub:

```python
# Create a subscription to receive all message events in a space
from googleapiclient.discovery import build

def create_message_subscription(space_name, pubsub_topic):
    service = build('workspaceevents', 'v1', credentials=creds)
    
    subscription = {
        'targetResource': f'//chat.googleapis.com/{space_name}',
        'eventTypes': ['google.workspace.chat.message.v1.created'],
        'notificationEndpoint': {
            'pubsubTopic': pubsub_topic  # e.g. 'projects/my-project/topics/chat-events'
        },
        'payloadOptions': {
            'includeResource': True  # Include full message data
        }
    }
    
    result = service.subscriptions().create(body=subscription).execute()
    return result
```

Then set up a Pub/Sub subscriber to process events:

```python
from google.cloud import pubsub_v1

def handle_message_event(message):
    event_data = json.loads(message.data)
    # event_data contains the full message object
    # Process message, check against your database, reply as needed
    message.ack()

subscriber = pubsub_v1.SubscriberClient()
subscription_path = 'projects/my-project/subscriptions/chat-subscription'
subscriber.subscribe(subscription_path, callback=handle_message_event)
```

**Advantages**:
- ✅ **Real-time event delivery** (near-instant, not polling)
- ✅ **Receives all messages** in the space (not just @mentions)
- ✅ **Efficient**: Only notified when messages actually occur
- ✅ **Scalable**: Pub/Sub handles high-volume scenarios
- ✅ **Event-driven architecture**: Clean separation of concerns
- ✅ **Reliable**: Pub/Sub provides message persistence and retry logic

**Disadvantages**:
- ❌ More complex setup (requires Cloud Pub/Sub configuration)
- ❌ Additional infrastructure (Pub/Sub topic + subscriber service)
- ❌ Requires Google Cloud project with Pub/Sub enabled
- ❌ Potential Pub/Sub costs (though generally minimal for chat volumes)

**Requirements**:
- The bot (or authorizing user) must be a **member of the space**
- Required scopes: `chat.messages.readonly` or `chat.messages`
- Google Cloud project with Pub/Sub API enabled
- Service account or user authentication for creating subscriptions

**Cost Considerations**:
- **Workspace Events API**: FREE
- **Google Chat API**: FREE
- **Cloud Pub/Sub**: [Pricing tiers](https://cloud.google.com/pubsub/pricing)
  - First 10 GB/month: FREE
  - Typical chat usage: Well within free tier (chat messages are small)
  - Example: 10,000 messages/day × 365 days × 2 KB/message = ~7.3 GB/year (FREE)

**Documentation**:
- [Subscribe to Google Chat events](https://developers.google.com/workspace/events/guides/events-chat)
- [Work with events from Google Chat](https://developers.google.com/workspace/chat/events-overview)
- [Video tutorial](https://www.youtube.com/watch?v=l9NuSk1ObJY)

#### Option 2: Accept the @Mention Limitation (Simplest)

Design your bot to work within the platform constraint:

- Users must @mention the bot to invoke it
- Use clear documentation to explain this requirement
- Consider adding slash commands for common actions

**Advantages**:
- ✅ **Simplest implementation** (no additional infrastructure)
- ✅ Works with push events (real-time)
- ✅ Clear user intent (user explicitly invokes bot)
- ✅ No additional costs or infrastructure

**Disadvantages**:
- ❌ Cannot proactively respond to messages
- ❌ Cannot implement "monitor and alert" use cases
- ❌ Users must remember to @mention the bot

**Best for**: Simple conversational bots where proactive monitoring isn't needed.

#### Option 3: Polling via Chat API (Not Recommended)

Use `spaces().messages().list()` to periodically fetch messages. **This approach is included for completeness but is not recommended** given the availability of the Events API.

**Why not recommended**: Polling is inefficient, introduces latency, wastes API quota, and requires complex state management. The Events API provides a superior solution for all use cases where polling might be considered.

### Recommendation for This Project

**For monitoring all messages**: Use the **Google Workspace Events API** (Option 1) if your use case requires receiving all messages without @mentions. This provides real-time, event-driven monitoring with minimal infrastructure costs.

**For simple conversational bots**: Accept the @mention limitation (Option 2) if your bot only needs to respond when explicitly invoked.

The architectural evaluation in this document now includes three viable approaches:

1. **For chat messages when @mentioned**: Bot endpoint receives push events (standard bot behavior)
2. **For monitoring all messages**: Google Workspace Events API + Cloud Pub/Sub (recommended for proactive monitoring)
3. **For external triggers**: Webhooks to post notifications or invoke bot logic

### Updated OAuth Scopes

Your bot needs these scopes:

```json
{
  "scopes": [
    "https://www.googleapis.com/auth/chat.messages",
    "https://www.googleapis.com/auth/chat.messages.readonly",
    "https://www.googleapis.com/auth/chat.spaces"
  ]
}
```

Note: `chat.messages.readonly` allows the bot to **read** messages via the API (polling), but does **not** cause the bot to receive push events for all messages.

---

## Recommendations

### Decision Matrix

| Use Case | Recommended Option | Rationale |
|----------|-------------------|-----------|
| Simple conversational bot (responds when mentioned) | **Option A** | Simplest implementation, lowest cost, sufficient for user-initiated interactions |
| Proactive monitoring (track all messages for alerts, compliance) | **Option B** | Real-time event delivery for all messages, efficient filtering, modern approach |
| Complex multi-source system (chat + external triggers + monitoring) | **Option C** | Unified dispatcher provides maximum flexibility and clean architecture |
| Low message volume (< 50/day) | **Option A** | Over-engineering not justified |
| Medium volume (50-500/day) with proactive needs | **Option B** | Sweet spot for Events API benefits |
| High volume (> 500/day) with complex requirements | **Option C** | Scalability and separation of concerns justify complexity |

### For This Project (Google Spaces Tasks Reporter)

**Current state**: The project uses a simple bot endpoint (`google_chat_app.cgi` → `chatbot/handler.py`) that responds to @mentions with "Hey, hello".

**Usage pattern**: Primarily a reporting/monitoring tool that analyzes task completion in Google Chat spaces.

**Recommendation**: **Migrate to Option B (Events API)** for the following reasons:

1. **Task monitoring requires seeing all messages**: To track task creation, updates, and completion, the bot needs to see all messages, not just mentions

2. **Proactive reporting**: The system should be able to identify tasks and track them without requiring users to @mention the bot

3. **Real-time responsiveness**: Events API provides near-instant notification of new messages without polling overhead

4. **Cost-effective**: Within free tier for typical usage volumes, and avoids expensive polling operations

5. **Future-proof**: As requirements grow (LLM integration, smart task recognition), the Events API infrastructure will already be in place

**Migration Path**:

**Phase 1** (current): Keep existing @mention bot for basic interactions
- Low-risk baseline functionality
- Users can explicitly invoke the bot

**Phase 2** (recommended next step): Add Google Workspace Events API subscription
- Subscribe to `google.workspace.chat.message.v1.created` events
- Set up Cloud Pub/Sub topic and subscriber
- Implement event handler to process all messages
- Filter for task-related messages (e.g., containing keywords like "task", "todo", "assigned")
- Log and analyze task mentions for reporting

**Phase 3** (future enhancement): Add sophisticated filtering and processing
- Implement LLM-based task recognition
- Automatic task tracking and completion analysis
- Proactive reminders for overdue tasks

**Phase 4** (if needed): Add external system integrations
- Webhook for booking system events
- IoT sensor integrations
- Evolve toward Option C if complexity justifies it

### General Recommendations

**Start with Option A if**:
- You're building a simple conversational bot
- Users will explicitly @mention the bot for all interactions
- Message volume is low (< 50/day)
- Development resources are limited

**Use Option B if**:
- You need to monitor all messages proactively
- Your use case includes alerts, compliance, or automated tracking
- Message volume is moderate to high
- You can invest in Pub/Sub infrastructure setup

**Consider Option C if**:
- You have multiple event sources (chat mentions, all messages, external systems)
- Your system requires sophisticated routing logic
- You need maximum scalability and separation of concerns
- You have a team that can manage the complexity

### Platform Evolution Note

The Google Workspace Events API represents a **significant evolution** in Google Chat's capabilities. Prior to its introduction, developers had only two options:

1. @mention-only bots (limited proactive capability)
2. Polling via Chat API (inefficient, introduces latency)

The Events API provides a modern, event-driven architecture that enables real-time monitoring without polling overhead. **For any new project requiring proactive monitoring, Option B (Events API) should be the default choice**.

---

## Conclusion

Google Chat's platform provides three distinct architectural patterns for bot integration, each suited to different use cases:

**Key takeaways**:

1. ✅ **Platform constraints**: Only Chat Apps can receive message events (webhooks cannot)
2. ✅ **@Mention-only bots** (Option A): Simplest approach, but limited to user-initiated interactions
3. ✅ **Workspace Events API** (Option B): Modern solution for monitoring all messages in real-time via Pub/Sub
4. ✅ **For proactive monitoring**: Option B (Events API) is the recommended approach as of 2024
5. ✅ **Cost**: Google APIs are free; Pub/Sub likely within free tier; costs come from processing infrastructure
6. ✅ **Efficiency**: Event-driven architecture with filtering before heavy processing reduces costs
7. ✅ **Security**: All options require similar security measures; Pub/Sub adds message authenticity verification
8. ✅ **Simplicity**: Option A is simplest; Option B adds moderate complexity; Option C is most complex
9. ✅ **Scalability**: Pub/Sub provides excellent scalability for high-volume scenarios
10. ✅ **Polling is obsolete**: The Events API eliminates the need for polling in almost all use cases

**Architecture Selection Guide**:

- **Simple conversational bot**: Option A (mention-only)
- **Proactive monitoring/tracking**: Option B (Events API)  ← **Recommended for most new projects**
- **Complex multi-source systems**: Option C (Hybrid Dispatcher)

**For the Google Spaces Tasks Reporter project**: **Option B (Events API)** is strongly recommended because task monitoring inherently requires seeing all messages, not just explicit mentions. The Events API provides the real-time, event-driven architecture needed for effective task tracking.

**Final note**: The introduction of the Google Workspace Events API fundamentally changes the architectural landscape for Google Chat bots. Prior workarounds (polling, mention-only) are now obsolete for use cases requiring comprehensive message monitoring. The Events API should be considered the default choice for any bot requiring proactive monitoring capabilities.

---

## References

### Google Workspace Events API (Primary Resource)

- [Work with events from Google Chat](https://developers.google.com/workspace/chat/events-overview) - Overview of event-driven architecture
- [Subscribe to Google Chat events](https://developers.google.com/workspace/events/guides/events-chat) - Detailed guide for implementing subscriptions
- [Google Workspace Events API](https://developers.google.com/workspace/events) - Complete Events API documentation
- [Video: Subscribe to Google Chat events](https://www.youtube.com/watch?v=l9NuSk1ObJY) - Official video tutorial

### Google Chat API Documentation

- [Choose a Google Chat app architecture](https://developers.google.com/chat/design) - Official architectural guidance
- [Build a Google Chat app as a webhook](https://developers.google.com/workspace/chat/quickstart/webhooks) - Webhook limitations and capabilities
- [Receive and respond to interaction events](https://developers.google.com/chat/api/guides/message-formats) - Event handling documentation
- [Google Chat API Scopes](https://developers.google.com/identity/protocols/oauth2/scopes#chat) - Required OAuth scopes

### Google Cloud Platform

- [Google Cloud Pub/Sub Pricing](https://cloud.google.com/pubsub/pricing) - Cost information (free tier available)
- [Google Workspace API quotas](https://developers.google.com/workspace/chat/quotas) - Rate limits and quotas (APIs are free)
- [Cloud Pub/Sub Documentation](https://cloud.google.com/pubsub/docs) - Technical documentation for Pub/Sub setup

### Community Resources

- [Stack Overflow: Google Chat bot message events](https://stackoverflow.com) - Community discussions about bot capabilities and limitations
- Various discussions confirm that bots receive only @mentions by default, unless using the Events API

---

**Document Version**: 2.0  
**Last Updated**: 2025-11-02  
**Major Changes**: Added Google Workspace Events API as recommended approach for monitoring all messages; deprecated polling method; updated all architectural recommendations  
**Author**: Architectural evaluation for Google Spaces Tasks Reporter project


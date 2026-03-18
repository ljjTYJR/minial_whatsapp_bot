# Design Framework

## Philosophy

Build the thinnest possible agentic bot: one channel, one model, one tool registry.
Each layer has a single responsibility and communicates through a narrow interface.

---

## Layers

```
Channel  →  Bot  →  Model  →  Tools
```

| Layer | Responsibility |
|-------|---------------|
| **Channel** | Speak a messaging protocol; deliver normalized messages in, send replies out |
| **Bot** | Route messages; manage per-user conversation state |
| **Model** | Run the agentic inference loop; call tools until a text reply is ready |
| **Tools** | Execute side effects on behalf of the model (filesystem, shell, APIs) |

Each layer depends only on the interface of the layer below it — not its implementation.

---

## Interfaces

### Channel Interface

A channel must provide two operations:

```
receive() → Message { sender, content, id }
send(sender, reply: Text | Image)
```

The channel owns protocol details (auth, reconnect, dedup, encoding).
The bot never sees protocol-specific types.

### Bot Interface

The bot is a message handler:

```
handle(message: Message) → void
```

Internally it holds:
- **Router** — maps message content to an action (command, camera, LLM)
- **History** — per-sender list of `{role, content}` turns
- **Skills** — discrete capabilities beyond plain LLM (e.g. camera, live stream)

### Model Interface

```
generate(history: Turn[]) → str
```

The model is stateless. All context is passed in via `history`.
Tool-calling is an internal detail of the model — the bot only sees the final text.

### Tool Interface

```
name: str
description: str
schema: {param: type, ...}
run(args) → str
```

Tools are registered in a flat registry. The model discovers them via schema at call time.
Every tool returns a string — results feed back into the model's context.

---

## Skills

A **skill** is a self-contained capability the bot routes to before the LLM.
Skills bypass the model entirely — they respond directly.

```
Skill {
    triggers: set[str]       # words or commands that activate this skill
    handle(message) → Reply  # produce a reply without calling the LLM
}
```

Current skills:

| Skill | Triggers | Reply type |
|-------|----------|------------|
| Snapshot | `photo`, `pic`, `snapshot`, `camera`, `stream` | Image |
| Live stream | `/live`, `live`, `feed`, `watch`, `webrtc`, `hls`, `rtsp` | Text (URLs) |

The routing order is:
```
slash command → skill triggers → LLM
```

Skills are checked before the LLM so they are always fast and deterministic.

---

## State

| Scope | What is stored | Owner |
|-------|---------------|-------|
| Per-sender | Conversation history (turn list) | Bot |
| Global | Latest camera frame | Camera skill |
| Session | Tunnel URL | Live stream skill |
| Ephemeral | In-flight tool results | Model |

There is no persistent storage. Restarting the bot clears all history.

---

## Extension Model

To add a **new channel**: implement `receive` / `send`, normalize to `Message`.

To add a **new skill**: define triggers and a `handle` function; register in the router before the LLM fallback.

To add a **new tool**: add an entry to the tool registry with name, description, schema, and `run`. The model picks it up automatically.

To swap the **model**: replace `generate(history) → str`. The rest of the stack is unaffected.

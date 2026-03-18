# my_bot

A minimal agentic WhatsApp bot with OAK-D camera integration.

## Architecture

```
Node.js bridge (bridge/)  ←WebSocket→  whatsapp.py  ←→  OpenRouterModel
                                              ↕                  ↕
                                         skill/             tool/
                                   (snapshot, live)    (read/write/bash…)
```

## Setup

**Prerequisites:** Python 3.12+, Node.js 20+, OAK-D depth camera (optional)

```bash
# Python deps
uv sync

# Node bridge
cd bridge && npm install && npm run build
```

Create `.env` in the project root:

```env
OPENROUTER_API_KEY=sk-or-...

# Optional: security
BRIDGE_TOKEN=      # shared secret between bridge and bot; leave empty to disable
ALLOW_FROM=        # comma-separated phone allowlist (e.g. 4915112345678); empty = allow all

# Optional: manual stream URLs (sent when user asks for live feed)
LIVE_STREAM_URL=
LIVE_STREAM_HLS_URL=
LIVE_STREAM_WEBRTC_URL=
LIVE_STREAM_RTSP_URL=

# Optional: auto MJPEG stream + Cloudflare tunnel (default: enabled)
AUTO_LIVE_STREAM=1              # set 0 to disable auto-start
AUTO_LIVE_HOST=127.0.0.1
AUTO_LIVE_PORT=8008
AUTO_LIVE_TUNNEL_PROTOCOL=http2 # passed to cloudflared --protocol

# Optional: OAK-D camera settings
OAK_FRAME_WIDTH=640
OAK_FRAME_HEIGHT=360
OAK_STREAM_FPS=30
OAK_JPEG_QUALITY=80             # 10–100
```

## Run

**Terminal 1 — WhatsApp bridge:**

```bash
cd bridge && node dist/index.js
```

Scan the QR code in WhatsApp → Linked Devices on first run. Auth is saved to `bridge/wa_auth/`.

**Terminal 2 — bot:**

```bash
uv run python main.py --whatsapp
```

CLI mode (no WhatsApp):

```bash
uv run python main.py
```

## WhatsApp commands

| Trigger | Action |
|---------|--------|
| `/live` | Reply with live stream links |
| `live`, `feed`, `watch`, `stream`, `webrtc`, `hls`, `rtsp` (keyword) | Same as `/live` |
| `stream`, `camera`, `snapshot`, `photo`, `pic` (keyword) | Capture and send a JPEG from the OAK-D RGB camera |
| anything else | Forward to LLM and reply |

### Camera snapshot

Sending a message containing `photo`, `snapshot`, `pic`, `camera`, or `stream` triggers a single JPEG capture from the OAK-D (CAM_A, RGB). If the MJPEG service is already running the cached frame is reused (no device conflict).

### Live stream (`/live`)

1. If `LIVE_STREAM_URL` (or the HLS/WebRTC/RTSP variants) is set in `.env`, those URLs are returned directly.
2. Otherwise, if `AUTO_LIVE_STREAM=1` (default), the bot:
   - Starts an MJPEG HTTP server on `AUTO_LIVE_HOST:AUTO_LIVE_PORT` (default `127.0.0.1:8008`)
   - Launches `cloudflared tunnel` to expose it publicly
   - Returns the public Cloudflare URL to the user

   Endpoints served:
   - `/` — simple HTML viewer page
   - `/stream.mjpg` — raw MJPEG stream

   Requires `cloudflared` to be installed and on `$PATH`.

## Project layout

```
whatsapp.py        # channel: message routing + WebSocket client
model.py           # OpenRouterModel with agentic tool-calling loop
skill/
  snapshot.py      # snapshot skill: single JPEG capture from OAK-D
  live.py          # live skill: MJPEG server + Cloudflare tunnel
  oak_view.py      # helper: view OAK-D RGB in an OpenCV window
  oak_stereo.py    # helper: view left/right mono feeds side-by-side
tool/
  __init__.py      # LLM tools: read, write, edit, glob, grep, bash
bridge/            # Node.js Baileys bridge (WhatsApp ↔ WebSocket)
```

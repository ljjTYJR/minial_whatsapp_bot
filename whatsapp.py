"""WhatsApp channel via Node.js Baileys bridge.

Architecture:
    Node.js bridge (bridge/) ←WebSocket→ this module ←→ OpenRouterModel

Setup:
    cd bridge && npm install && npm run build
    node dist/index.js          # terminal 1 — scan QR on first run
    python main.py --whatsapp   # terminal 2
"""
import asyncio
import json
import logging
import os
from collections import OrderedDict

import websockets

from model import OpenRouterModel

BRIDGE_URL = os.getenv("BRIDGE_URL", "ws://127.0.0.1:8765")
BRIDGE_TOKEN = os.getenv("BRIDGE_TOKEN", "")
ALLOW_FROM = set(filter(None, os.getenv("ALLOW_FROM", "").split(",")))  # comma-separated phone numbers

log = logging.getLogger("whatsapp")


class WhatsAppBot:
    def __init__(self):
        self.model = OpenRouterModel(log_level=logging.INFO)
        self.histories: dict[str, list] = {}          # sender → conversation history
        self.seen: OrderedDict[str, None] = OrderedDict()  # dedup last 1000 message ids
        self._ws = None

    async def run(self):
        log.info("Connecting to bridge at %s", BRIDGE_URL)
        while True:
            try:
                async with websockets.connect(BRIDGE_URL) as ws:
                    self._ws = ws
                    if BRIDGE_TOKEN:
                        await ws.send(json.dumps({"type": "auth", "token": BRIDGE_TOKEN}))
                    log.info("Connected to WhatsApp bridge")
                    async for raw in ws:
                        await self._handle(raw)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._ws = None
                log.warning("Bridge disconnected: %s — retrying in 5s", e)
                await asyncio.sleep(5)

    async def _handle(self, raw: str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = data.get("type")

        if msg_type == "message":
            await self._on_message(data)
        elif msg_type == "status":
            log.info("WhatsApp status: %s", data.get("status"))
        elif msg_type == "qr":
            log.info("QR code received — scan in the bridge terminal")
        elif msg_type == "error":
            log.error("Bridge error: %s", data.get("error"))

    async def _on_message(self, data: dict):
        msg_id = data.get("id", "")
        if msg_id:
            if msg_id in self.seen:
                return
            self.seen[msg_id] = None
            if len(self.seen) > 1000:
                self.seen.popitem(last=False)

        sender = data.get("sender", "")       # full JID e.g. 1234567890@s.whatsapp.net
        pn = data.get("pn", "")
        chat_id = sender                       # reply to same JID
        phone = (pn or sender).split("@")[0]
        content = data.get("content", "").strip()

        if not content or not chat_id:
            return

        # Allowlist check
        if ALLOW_FROM and phone not in ALLOW_FROM:
            log.info("Ignored message from %s (not in ALLOW_FROM)", phone)
            return

        log.info("Message from %s: %s", phone, content[:80])

        hist = self.histories.setdefault(chat_id, [])
        hist.append({"role": "user", "content": content})

        # Run blocking model call in thread pool to keep event loop free
        reply = await asyncio.get_event_loop().run_in_executor(
            None, self.model.generate_response, hist
        )
        hist.append({"role": "assistant", "content": reply})

        await self._send(chat_id, reply)

    async def _send(self, to: str, text: str):
        if not self._ws:
            log.warning("Not connected, cannot send")
            return
        try:
            await self._ws.send(json.dumps({"type": "send", "to": to, "text": text}, ensure_ascii=False))
        except Exception as e:
            log.error("Send failed: %s", e)


def run_bot():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(WhatsAppBot().run())

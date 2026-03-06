"""WhatsApp channel via Node.js Baileys bridge.

Architecture:
    Node.js bridge (bridge/) ←WebSocket→ this module ←→ OpenRouterModel

Setup:
    cd bridge && npm install && npm run build
    node dist/index.js          # terminal 1 — scan QR on first run
    python main.py --whatsapp   # terminal 2
"""
import asyncio
import base64
import json
import logging
import os
from collections import OrderedDict

import cv2
import depthai as dai
import websockets

from model import OpenRouterModel

BRIDGE_URL = os.getenv("BRIDGE_URL", "ws://127.0.0.1:8765")
BRIDGE_TOKEN = os.getenv("BRIDGE_TOKEN", "")
ALLOW_FROM = set(filter(None, os.getenv("ALLOW_FROM", "").split(",")))  # comma-separated phone numbers

log = logging.getLogger("whatsapp")


STREAM_TRIGGERS = {"stream", "camera", "snapshot", "photo", "pic"}


def capture_oak_frame() -> bytes:
    """Capture a single JPEG frame from OAK-D RGB camera."""
    p = dai.Pipeline()
    cam = p.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_A)
    q = cam.requestOutput((640, 360), dai.ImgFrame.Type.BGR888p).createOutputQueue()
    p.start()
    try:
        frame = q.get().getCvFrame()
    finally:
        p.stop()
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return buf.tobytes()


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

        # Check for camera trigger words
        words = set(content.lower().split())
        if words & STREAM_TRIGGERS:
            await self._send(chat_id, "Capturing frame from OAK-D...")
            try:
                jpeg = await asyncio.get_event_loop().run_in_executor(None, capture_oak_frame)
                await self._send_image(chat_id, jpeg, "OAK-D RGB snapshot")
            except Exception as e:
                log.error("Camera capture failed: %s", e)
                await self._send(chat_id, f"Camera error: {e}")
            return

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

    async def _send_image(self, to: str, jpeg: bytes, caption: str = ""):
        if not self._ws:
            log.warning("Not connected, cannot send image")
            return
        try:
            payload = {"type": "send_image", "to": to, "image": base64.b64encode(jpeg).decode(), "caption": caption}
            await self._ws.send(json.dumps(payload))
        except Exception as e:
            log.error("Send image failed: %s", e)


def run_bot():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(WhatsAppBot().run())

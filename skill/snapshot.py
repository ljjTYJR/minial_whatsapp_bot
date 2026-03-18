"""Snapshot skill: capture a single JPEG frame from OAK-D RGB camera."""
import asyncio
import threading
import time

import cv2
import depthai as dai

_OAK_LOCK = threading.Lock()  # serialise all DAI pipeline open/close calls

TRIGGERS = {"stream", "camera", "snapshot", "photo", "pic"}

OAK_W, OAK_H = 640, 360
JPEG_Q = 80


def capture_frame() -> bytes:
    """Capture one JPEG frame from OAK-D RGB (blocks, holds _OAK_LOCK)."""
    with _OAK_LOCK:
        p = dai.Pipeline()
        cam = p.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_A)
        q = cam.requestOutput((OAK_W, OAK_H), dai.ImgFrame.Type.BGR888p).createOutputQueue()
        p.start()
        try:
            frame = q.get().getCvFrame()
        finally:
            p.stop()
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_Q])
    return buf.tobytes()


async def run(bot, chat_id: str, mjpeg=None) -> None:
    """Send an OAK-D snapshot to chat_id. Uses mjpeg cache if streaming."""
    await bot.send(chat_id, "Capturing frame from OAK-D...")
    try:
        if mjpeg and mjpeg.is_running():
            def wait():
                deadline = time.time() + 3
                while time.time() < deadline:
                    f = mjpeg.latest_jpeg
                    if f:
                        return f
                    time.sleep(0.05)
                raise RuntimeError("Timed out waiting for MJPEG frame")
            jpeg = await asyncio.get_event_loop().run_in_executor(None, wait)
        else:
            jpeg = await asyncio.get_event_loop().run_in_executor(None, capture_frame)
        await bot.send_image(chat_id, jpeg, "OAK-D RGB snapshot")
    except Exception as e:
        await bot.send(chat_id, f"Camera error: {e}")

"""Live stream skill: MJPEG server + Cloudflare tunnel."""
import asyncio
import logging
import re
import threading
import time
from http import server

import cv2
import depthai as dai
from dotenv import load_dotenv
import os

from skill.snapshot import _OAK_LOCK, OAK_W, OAK_H, JPEG_Q

log = logging.getLogger("skill.live")

TRIGGERS = {"live", "livestream", "watch", "feed", "hls", "webrtc", "rtsp"}

HOST = os.getenv("AUTO_LIVE_HOST", "127.0.0.1").strip()
PORT = int(os.getenv("AUTO_LIVE_PORT", "8008").strip())
TUNNEL_PROTOCOL = os.getenv("AUTO_LIVE_TUNNEL_PROTOCOL", "http2").strip() or "http2"
AUTO_LIVE = os.getenv("AUTO_LIVE_STREAM", "1").strip() not in {"0", "false", "False"}
STREAM_FPS = max(1, int(os.getenv("OAK_STREAM_FPS", "30").strip()))


class MjpegService:
    def __init__(self, host: str = HOST, port: int = PORT):
        self.host, self.port = host, port
        self._latest_jpeg: bytes | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._capture_thread: threading.Thread | None = None
        self._server_thread: threading.Thread | None = None
        self._httpd: server.ThreadingHTTPServer | None = None

    @property
    def local_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def latest_jpeg(self) -> bytes | None:
        with self._lock:
            return self._latest_jpeg

    def is_running(self) -> bool:
        return bool(self._capture_thread and self._capture_thread.is_alive())

    def start(self) -> None:
        if self.is_running():
            return
        if self._capture_thread:
            self._capture_thread.join(timeout=5)
        self._stop.clear()
        self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True, name="oak-capture")
        self._capture_thread.start()
        self._httpd = self._make_server()
        self._server_thread = threading.Thread(target=self._httpd.serve_forever, daemon=True, name="oak-http")
        self._server_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._capture_thread:
            self._capture_thread.join(timeout=5)
            self._capture_thread = None

    def _capture_loop(self) -> None:
        with _OAK_LOCK:
            p = dai.Pipeline()
            cam = p.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_A)
            q = cam.requestOutput((OAK_W, OAK_H), dai.ImgFrame.Type.BGR888p).createOutputQueue()
            p.start()
            try:
                while not self._stop.is_set() and p.isRunning():
                    frame = q.get().getCvFrame()
                    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_Q])
                    if ok:
                        with self._lock:
                            self._latest_jpeg = buf.tobytes()
            except Exception as exc:
                log.error("OAK capture failed: %s", exc)
            finally:
                p.stop()

    def _make_server(self) -> server.ThreadingHTTPServer:
        parent = self

        class Handler(server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path in ("/", "/index.html"):
                    body = (
                        "<html><body style='margin:0;background:#111;color:#fff'>"
                        "<div style='padding:12px'>OAK-D Live</div>"
                        "<img src='/stream.mjpg' style='width:100%'/>"
                        "</body></html>"
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/stream.mjpg":
                    self.send_response(200)
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.end_headers()
                    try:
                        while not parent._stop.is_set():
                            frame = parent.latest_jpeg
                            if frame is None:
                                time.sleep(0.05)
                                continue
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n")
                            self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode())
                            self.wfile.write(frame + b"\r\n")
                            time.sleep(1.0 / STREAM_FPS)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                else:
                    self.send_error(404)

            def log_message(self, *args):
                pass

        return server.ThreadingHTTPServer((self.host, self.port), Handler)


class CloudflaredTunnel:
    _URL_RE = re.compile(r"https://[a-zA-Z0-9.-]+\.trycloudflare\.com")

    def __init__(self):
        self._proc: asyncio.subprocess.Process | None = None
        self._url: str | None = None

    async def ensure(self, local_url: str) -> str | None:
        if self._proc and self._proc.returncode is None and self._url:
            return self._url
        self._proc = await asyncio.create_subprocess_exec(
            "cloudflared", "tunnel", "--url", local_url,
            "--protocol", TUNNEL_PROTOCOL, "--no-autoupdate",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        assert self._proc.stdout
        deadline = time.time() + 20
        while time.time() < deadline:
            line = await self._proc.stdout.readline()
            if not line:
                break
            m = self._URL_RE.search(line.decode("utf-8", errors="ignore"))
            if m:
                self._url = m.group(0)
                await asyncio.sleep(3)
                return self._url
        await self.stop()
        return None

    async def stop(self):
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            await self._proc.wait()
        self._proc = None
        self._url = None


def _static_urls() -> str | None:
    """Return configured LIVE_STREAM_* URLs if set, else None."""
    load_dotenv(override=True)
    lines = []
    for env, label in [
        ("LIVE_STREAM_URL", "Live stream"),
        ("LIVE_STREAM_HLS_URL", "HLS"),
        ("LIVE_STREAM_WEBRTC_URL", "WebRTC"),
        ("LIVE_STREAM_RTSP_URL", "RTSP"),
    ]:
        v = os.getenv(env, "").strip()
        if v:
            lines.append(f"{label}: {v}")
    return ("Camera live links:\n" + "\n".join(lines)) if lines else None


async def run(bot, chat_id: str, mjpeg: MjpegService, tunnel: CloudflaredTunnel) -> None:
    """Send live stream links (static env > auto MJPEG+tunnel)."""
    msg = _static_urls()
    if msg:
        await bot.send(chat_id, msg)
        return

    if not AUTO_LIVE:
        await bot.send(chat_id, "Live stream URL not configured. Set LIVE_STREAM_URL.")
        return

    try:
        try:
            mjpeg.start()
        except OSError as exc:
            if exc.errno != 98:
                raise
            log.warning("Port %s in use, reusing existing service", mjpeg.port)
        public_url = await tunnel.ensure(mjpeg.local_url)
        if public_url:
            await bot.send(chat_id, f"Camera live links:\nLive stream: {public_url}\nMJPEG: {public_url}/stream.mjpg")
        else:
            await bot.send(chat_id, "Could not create Cloudflare tunnel. Install cloudflared or set LIVE_STREAM_URL.")
    except Exception as exc:
        log.error("Auto live start failed: %s", exc)
        await bot.send(chat_id, f"Live stream failed: {exc}")

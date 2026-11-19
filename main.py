py -3.13 -m pip install --upgrade pip
py -3.13 -m pip install aiohttp aiortc mss pillow psutil





# ============================================================
# HANA DESKTOP STREAM
# Standalone Desktop Screen Streaming Server
# Python 3.13 | Windows | WebRTC | VP8
# Optimized baseline: Ryzen 5 3500U / Vega 8 / RAM 8GB
# Port: 8002
# ============================================================

import asyncio
import ipaddress
import logging
import secrets
import socket
import threading
import time
import uuid
from fractions import Fraction

import mss
import psutil
from PIL import Image

from aiohttp import web
from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    RTCConfiguration,
    VideoStreamTrack,
)

# ============================================================
# CONFIGURATION
# ============================================================

APP_NAME = "HANA DESKTOP STREAM"
HOST = "0.0.0.0"
PORT = 8002

DEFAULT_FPS = 20
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720

MAX_VIEWERS = 1
MAX_SDP_SIZE = 1024 * 1024

TOKEN = secrets.token_urlsafe(32)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | HANA STREAM | %(levelname)s | %(message)s"
)

log = logging.getLogger("hana-stream")

sessions = {}
session_lock = asyncio.Lock()
capture_local = threading.local()

# ============================================================
# SCREEN CAPTURE
# ============================================================

QUALITY_PRESETS = {
    "480p": (854, 480),
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "native": (2560, 1440),
}


def get_capture():
    if not hasattr(capture_local, "sct"):
        capture_local.sct = mss.mss()
    return capture_local.sct


def get_monitors():
    with mss.mss() as sct:
        result = []

        for index, monitor in enumerate(sct.monitors):
            if index == 0:
                continue

            result.append({
                "id": index,
                "name": f"Monitor {index}",
                "width": monitor["width"],
                "height": monitor["height"],
                "left": monitor["left"],
                "top": monitor["top"],
            })

        return result


def capture_screen(monitor_id, max_width, max_height):
    sct = get_capture()

    if monitor_id <= 0 or monitor_id >= len(sct.monitors):
        raise ValueError("Invalid monitor")

    monitor = sct.monitors[monitor_id]

    shot = sct.grab(monitor)

    image = Image.frombytes(
        "RGB",
        shot.size,
        shot.bgra,
        "raw",
        "BGRX"
    )

    width, height = image.size

    scale = min(
        max_width / width,
        max_height / height,
        1.0
    )

    if scale < 1.0:
        new_size = (
            max(1, int(width * scale)),
            max(1, int(height * scale))
        )

        image = image.resize(
            new_size,
            Image.Resampling.BILINEAR
        )

    return image


# ============================================================
# WEBRTC VIDEO TRACK
# ============================================================

class DesktopTrack(VideoStreamTrack):

    kind = "video"

    def __init__(
        self,
        monitor_id=1,
        width=DEFAULT_WIDTH,
        height=DEFAULT_HEIGHT,
        fps=DEFAULT_FPS
    ):
        super().__init__()

        self.monitor_id = monitor_id
        self.width = width
        self.height = height

        self.fps = max(5, min(int(fps), 30))

        self.interval = 1.0 / self.fps
        self.next_frame = 0

        self.frame_index = 0
        self.frames_sent = 0
        self.last_frame_time = time.time()

        self._closed = False

    async def recv(self):

        if self._closed:
            raise asyncio.CancelledError()

        loop = asyncio.get_running_loop()

        now = loop.time()

        if self.next_frame == 0:
            self.next_frame = now

        delay = self.next_frame - now

        if delay > 0:
            await asyncio.sleep(delay)

        try:
            image = await asyncio.to_thread(
                capture_screen,
                self.monitor_id,
                self.width,
                self.height
            )

        except Exception as exc:
            log.error("Screen capture failed: %s", exc)
            await asyncio.sleep(0.5)
            raise

        from av import VideoFrame

        frame = VideoFrame.from_image(image)

        frame.pts = int(
            self.frame_index * 90000 / self.fps
        )

        frame.time_base = Fraction(1, 90000)

        self.frame_index += 1
        self.frames_sent += 1
        self.last_frame_time = time.time()

        self.next_frame += self.interval

        # Prevent accumulated timing drift.
        if loop.time() - self.next_frame > self.interval * 2:
            self.next_frame = loop.time() + self.interval

        return frame

    def stop(self):
        self._closed = True
        super().stop()


# ============================================================
# SECURITY
# ============================================================

def is_lan_address(remote):
    if not remote:
        return False

    try:
        address = ipaddress.ip_address(
            remote.split("%")[0]
        )

        return (
            address.is_private
            or address.is_loopback
            or address.is_link_local
        )

    except ValueError:
        return False


@web.middleware
async def security_middleware(request, handler):

    remote = request.remote

    if not is_lan_address(remote):
        return web.json_response(
            {"error": "LAN access only"},
            status=403
        )

    # Only API routes require the access token.
    if request.path.startswith("/api/"):

        auth = request.headers.get(
            "Authorization", ""
        )

        supplied = auth.removeprefix("Bearer ").strip()

        if not secrets.compare_digest(supplied, TOKEN):
            return web.json_response(
                {"error": "Unauthorized"},
                status=401
            )

    response = await handler(request)

    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"

    return response


# ============================================================
# HTML INTERFACE
# ============================================================

HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport"
 content="width=device-width, initial-scale=1, maximum-scale=1">

<title>HANA DESKTOP STREAM</title>

<style>
:root {
    color-scheme: dark;
    --bg: #090a0d;
    --panel: #14161b;
    --border: #282b32;
    --text: #f5f5f7;
    --muted: #9297a3;
    --blue: #4785e8;
}

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    padding: 18px;
    background: var(--bg);
    color: var(--text);
    font-family: system-ui, -apple-system, sans-serif;
}

.container {
    max-width: 1100px;
    margin: auto;
}

header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    margin-bottom: 20px;
}

h1 {
    font-size: 19px;
    letter-spacing: 1px;
    margin: 0;
}

.subtitle {
    color: var(--muted);
    font-size: 12px;
    margin-top: 5px;
}

.panel {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 15px;
    margin-bottom: 14px;
}

label {
    display: block;
    color: var(--muted);
    font-size: 12px;
    margin-bottom: 7px;
}

input, select, button {
    width: 100%;
    min-height: 43px;
    padding: 10px 12px;
    border-radius: 9px;
    border: 1px solid var(--border);
    background: #1c1f26;
    color: white;
    font-size: 14px;
}

button {
    cursor: pointer;
    font-weight: 600;
}

button.primary {
    background: var(--blue);
    border-color: var(--blue);
}

button:disabled {
    opacity: .5;
    cursor: not-allowed;
}

.controls {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
}

.actions {
    display: flex;
    gap: 10px;
    margin-top: 15px;
}

.actions button {
    flex: 1;
}

.video-box {
    background: #000;
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
    position: relative;
    min-height: 180px;
    display: flex;
    justify-content: center;
    align-items: center;
}

video {
    width: 100%;
    height: auto;
    max-height: 80vh;
    display: block;
    object-fit: contain;
}

.video-placeholder {
    color: #777;
    text-align: center;
    padding: 35px;
    font-size: 13px;
}

.stats {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 10px;
}

.stat {
    background: #1c1f26;
    border-radius: 9px;
    padding: 12px;
}

.stat small {
    color: var(--muted);
    display: block;
    font-size: 11px;
}

.stat strong {
    display: block;
    margin-top: 6px;
    font-size: 18px;
    font-variant-numeric: tabular-nums;
}

.status {
    color: var(--muted);
    font-size: 12px;
}

.dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #777;
    margin-right: 6px;
}

.dot.online {
    background: #39c78a;
}

@media(max-width:650px) {
    body {
        padding: 12px;
    }

    .controls {
        grid-template-columns: 1fr 1fr;
    }

    .stats {
        grid-template-columns: 1fr 1fr;
    }

    h1 {
        font-size: 16px;
    }
}

video:fullscreen {
    width: 100%;
    height: 100%;
    max-height: none;
    object-fit: contain;
    background: black;
}

</style>
</head>

<body>
<div class="container">

<header>
    <div>
        <h1>HANA DESKTOP STREAM</h1>
        <div class="subtitle">Private local desktop streaming</div>
    </div>
    <div class="status">
        <span class="dot" id="dot"></span>
        <span id="status">Disconnected</span>
    </div>
</header>

<div class="panel">

    <label>Access token</label>
    <input id="token" type="password"
    placeholder="Paste server access token">

    <div class="actions">
        <button onclick="saveToken()">Save token</button>
    </div>

</div>

<div class="panel">

    <div class="controls">

        <div>
            <label>Monitor</label>
            <select id="monitor"></select>
        </div>

        <div>
            <label>Quality</label>
            <select id="quality">
                <option value="480p">480p</option>
                <option value="720p" selected>720p</option>
                <option value="1080p">1080p</option>
                <option value="native">Native (max 1440p)</option>
            </select>
        </div>

        <div>
            <label>Frame rate</label>
            <select id="fps">
                <option value="10">10 FPS</option>
                <option value="15">15 FPS</option>
                <option value="20" selected>20 FPS</option>
                <option value="24">24 FPS</option>
                <option value="30">30 FPS</option>
            </select>
        </div>

    </div>

    <div class="actions">
        <button class="primary" id="start"
        onclick="startStream()">Start stream</button>

        <button id="stop" onclick="stopStream()">Stop</button>

        <button onclick="fullScreen()">Fullscreen</button>
    </div>

</div>

<div class="panel">
    <div class="video-box" id="videoBox">
        <div class="video-placeholder" id="placeholder">
            Desktop preview will appear here
        </div>

        <video id="video" autoplay playsinline></video>
    </div>
</div>

<div class="panel">

    <label>LIVE STATISTICS</label>

    <div class="stats">

        <div class="stat">
            <small>FPS</small>
            <strong id="s-fps">--</strong>
        </div>

        <div class="stat">
            <small>Bitrate</small>
            <strong id="s-bitrate">--</strong>
        </div>

        <div class="stat">
            <small>Latency / RTT</small>
            <strong id="s-rtt">--</strong>
        </div>

        <div class="stat">
            <small>Received frames</small>
            <strong id="s-frames">--</strong>
        </div>

    </div>

</div>

<div class="panel">
    <label>CONNECTION</label>
    <div class="status" id="connectionInfo">
        Waiting for connection
    </div>
</div>

</div>

<script>

const $ = id => document.getElementById(id);

let pc = null;
let sessionId = null;
let manualStop = false;
let reconnectTimer = null;
let statsTimer = null;
let lastBytes = 0;
let lastStatsTime = 0;

const tokenInput = $("token");

tokenInput.value = localStorage.getItem("hana_token") || "";

function saveToken() {
    localStorage.setItem("hana_token", tokenInput.value.trim());
    alert("Token saved on this device.");
}

function headers() {
    return {
        "Authorization": "Bearer " + tokenInput.value.trim(),
        "Content-Type": "application/json"
    };
}

async function api(path, options = {}) {
    const response = await fetch(path, {
        ...options,
        headers: {
            ...headers(),
            ...(options.headers || {})
        }
    });

    if (!response.ok) {
        let message = response.statusText;

        try {
            const data = await response.json();
            message = data.error || message;
        } catch (_) {}

        throw new Error(message);
    }

    return response.json();
}

function setStatus(text, online = false) {
    $("status").textContent = text;
    $("dot").className = online ? "dot online" : "dot";
    $("connectionInfo").textContent = text;
}

async function loadMonitors() {
    const data = await api("/api/monitors");

    const select = $("monitor");
    select.innerHTML = "";

    data.monitors.forEach(m => {
        const option = document.createElement("option");
        option.value = m.id;
        option.textContent =
            `${m.name} (${m.width} × ${m.height})`;

        select.appendChild(option);
    });
}

async function startStream() {

    manualStop = false;
    clearTimeout(reconnectTimer);

    $("start").disabled = true;

    try {

        if (!tokenInput.value.trim()) {
            throw new Error("Enter access token first.");
        }

        await stopConnection();

        setStatus("Connecting...");

        await loadMonitors();

        pc = new RTCPeerConnection({
            iceServers: []
        });

        pc.addTransceiver("video", {
            direction: "recvonly"
        });

        pc.ontrack = event => {
            $("video").srcObject = event.streams[0];
            $("placeholder").style.display = "none";

            $("video").play().catch(() => {});
        };

        pc.onconnectionstatechange = () => {

            const state = pc.connectionState;

            if (state === "connected") {
                setStatus("Connected", true);
            }

            if (state === "failed" || state === "disconnected") {
                setStatus(state);

                if (!manualStop) {
                    scheduleReconnect();
                }
            }
        };

        pc.oniceconnectionstatechange = () => {
            if (pc && pc.iceConnectionState === "failed") {
                scheduleReconnect();
            }
        };

        const offer = await pc.createOffer();

        await pc.setLocalDescription(offer);

        const quality = $("quality").value;

        const payload = {
            sdp: pc.localDescription.sdp,
            type: pc.localDescription.type,
            monitor: Number($("monitor").value),
            quality: quality,
            fps: Number($("fps").value)
        };

        const answer = await api("/api/offer", {
            method: "POST",
            body: JSON.stringify(payload)
        });

        sessionId = answer.session_id;

        await pc.setRemoteDescription({
            type: answer.type,
            sdp: answer.sdp
        });

        setStatus("Negotiating...");

        startStats();

    } catch (error) {

        console.error(error);
        setStatus("Error: " + error.message);

        if (!manualStop) {
            scheduleReconnect();
        }

    } finally {
        $("start").disabled = false;
    }
}

function scheduleReconnect() {

    if (manualStop || reconnectTimer) return;

    reconnectTimer = setTimeout(() => {
        reconnectTimer = null;

        startStream();

    }, 3000);

    setStatus("Reconnecting in 3 seconds...");
}

async function stopConnection() {

    clearInterval(statsTimer);
    statsTimer = null;

    if (sessionId) {
        try {
            await api("/api/close/" + sessionId, {
                method: "POST"
            });
        } catch (_) {}
    }

    sessionId = null;

    if (pc) {
        pc.ontrack = null;
        pc.onconnectionstatechange = null;

        pc.close();
        pc = null;
    }

    $("video").srcObject = null;
    $("placeholder").style.display = "block";
}

async function stopStream() {

    manualStop = true;

    clearTimeout(reconnectTimer);
    reconnectTimer = null;

    await stopConnection();

    setStatus("Disconnected");
}

async function startStats() {

    clearInterval(statsTimer);

    lastBytes = 0;
    lastStatsTime = 0;

    statsTimer = setInterval(async () => {

        if (!pc || pc.connectionState !== "connected") return;

        try {

            const stats = await pc.getStats();

            let bytes = 0;
            let frames = 0;
            let fps = 0;
            let rtt = null;

            stats.forEach(report => {

                if (
                    report.type === "inbound-rtp" &&
                    (report.kind === "video" || report.mediaType === "video")
                ) {
                    bytes = report.bytesReceived || 0;
                    frames = report.framesReceived || 0;
                    fps = report.framesPerSecond || 0;
                }

                if (
                    report.type === "candidate-pair" &&
                    report.state === "succeeded" &&
                    report.nominated
                ) {
                    rtt = report.currentRoundTripTime;
                }

            });

            const now = performance.now();

            if (lastStatsTime && bytes >= lastBytes) {

                const elapsed = (now - lastStatsTime) / 1000;

                const bitrate =
                    ((bytes - lastBytes) * 8) / elapsed / 1000;

                $("s-bitrate").textContent =
                    bitrate.toFixed(0) + " kbps";
            }

            lastBytes = bytes;
            lastStatsTime = now;

            $("s-fps").textContent =
                fps ? fps.toFixed(1) : "--";

            $("s-frames").textContent = frames;

            $("s-rtt").textContent =
                rtt !== null
                ? Math.round(rtt * 1000) + " ms"
                : "--";

        } catch (error) {
            console.warn("Stats error", error);
        }

    }, 1000);
}

function fullScreen() {

    const video = $("video");

    if (video.requestFullscreen) {
        video.requestFullscreen();
    } else if (video.webkitEnterFullscreen) {
        video.webkitEnterFullscreen();
    }
}

window.addEventListener("beforeunload", () => {
    manualStop = true;

    if (pc) pc.close();
});

</script>

</body>
</html>
"""


# ============================================================
# API: MONITORS
# ============================================================

async def api_monitors(request):

    try:
        monitors = await asyncio.to_thread(get_monitors)

        return web.json_response({
            "monitors": monitors
        })

    except Exception as exc:
        log.exception("Monitor listing failed")

        return web.json_response(
            {"error": str(exc)},
            status=500
        )


# ============================================================
# API: CREATE STREAM
# ============================================================

async def api_offer(request):

    try:
        data = await request.json()

        sdp = data.get("sdp")
        sdp_type = data.get("type")

        if not isinstance(sdp, str) or len(sdp) > MAX_SDP_SIZE:
            raise ValueError("Invalid SDP")

        if sdp_type != "offer":
            raise ValueError("Only offers are accepted")

        quality = data.get("quality", "720p")

        if quality not in QUALITY_PRESETS:
            raise ValueError("Invalid quality")

        width, height = QUALITY_PRESETS[quality]

        # Native mode is capped to 1440p.
        if quality == "native":
            width, height = 2560, 1440

        fps = int(data.get("fps", DEFAULT_FPS))
        fps = max(5, min(fps, 30))

        monitor = int(data.get("monitor", 1))

        monitors = await asyncio.to_thread(get_monitors)

        valid_ids = [m["id"] for m in monitors]

        if monitor not in valid_ids:
            raise ValueError("Monitor not found")

        async with session_lock:

            if len(sessions) >= MAX_VIEWERS:
                raise ValueError("Maximum viewer limit reached")

            pc = RTCPeerConnection(
                RTCConfiguration(iceServers=[])
            )

            track = DesktopTrack(
                monitor_id=monitor,
                width=width,
                height=height,
                fps=fps
            )

            pc.addTrack(track)

            await pc.setRemoteDescription(
                RTCSessionDescription(
                    sdp=sdp,
                    type=sdp_type
                )
            )

            answer = await pc.createAnswer()

            await pc.setLocalDescription(answer)

            session_id = uuid.uuid4().hex

            sessions[session_id] = {
                "pc": pc,
                "track": track,
                "created": time.time(),
                "monitor": monitor,
                "quality": quality,
                "fps": fps
            }

        log.info(
            "Stream created: %s | Monitor %s | %s | %s FPS",
            session_id,
            monitor,
            quality,
            fps
        )

        return web.json_response({
            "session_id": session_id,
            "type": pc.localDescription.type,
            "sdp": pc.localDescription.sdp
        })

    except ValueError as exc:

        return web.json_response(
            {"error": str(exc)},
            status=400
        )

    except Exception as exc:

        log.exception("WebRTC negotiation failed")

        return web.json_response(
            {"error": str(exc)},
            status=500
        )


# ============================================================
# API: CLOSE STREAM
# ============================================================

async def api_close(request):

    session_id = request.match_info["session_id"]

    async with session_lock:

        session = sessions.pop(session_id, None)

    if session:

        session["track"].stop()

        await session["pc"].close()

        log.info("Session closed: %s", session_id)

    return web.json_response({
        "success": True
    })


# ============================================================
# API: SERVER STATUS
# ============================================================

async def api_status(request):

    cpu = psutil.cpu_percent(interval=None)

    memory = psutil.virtual_memory()

    active = []

    for sid, session in sessions.items():

        active.append({
            "session_id": sid,
            "monitor": session["monitor"],
            "quality": session["quality"],
            "fps": session["fps"],
            "frames_sent": session["track"].frames_sent,
            "connection": session["pc"].connectionState
        })

    return web.json_response({
        "name": APP_NAME,
        "cpu_percent": cpu,
        "ram_percent": memory.percent,
        "ram_used_mb": round(memory.used / 1024 / 1024),
        "active_sessions": len(active),
        "sessions": active
    })


# ============================================================
# HOME
# ============================================================

async def home(request):

    return web.Response(
        text=HTML,
        content_type="text/html"
    )


# ============================================================
# CLEANUP
# ============================================================

async def cleanup(app):

    log.info("Closing all streaming sessions...")

    async with session_lock:

        current = list(sessions.values())
        sessions.clear()

    for session in current:

        session["track"].stop()

        await session["pc"].close()

    log.info("HANA DESKTOP STREAM stopped.")


# ============================================================
# NETWORK
# ============================================================

def get_lan_ip():

    sock = socket.socket(
        socket.AF_INET,
        socket.SOCK_DGRAM
    )

    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]

    except OSError:
        return "127.0.0.1"

    finally:
        sock.close()


# ============================================================
# APPLICATION
# ============================================================

async def main():

    app = web.Application(
        middlewares=[security_middleware],
        client_max_size=MAX_SDP_SIZE
    )

    app.on_cleanup.append(cleanup)

    app.router.add_get("/", home)

    app.router.add_get(
        "/api/monitors",
        api_monitors
    )

    app.router.add_post(
        "/api/offer",
        api_offer
    )

    app.router.add_post(
        "/api/close/{session_id}",
        api_close
    )

    app.router.add_get(
        "/api/status",
        api_status
    )

    runner = web.AppRunner(
        app,
        access_log=None
    )

    await runner.setup()

    site = web.TCPSite(
        runner,
        HOST,
        PORT
    )

    await site.start()

    lan_ip = get_lan_ip()

    print("\n")
    print("=" * 58)
    print("       HANA DESKTOP STREAM")
    print("=" * 58)

    print(f"\n Local URL : http://127.0.0.1:{PORT}")
    print(f" LAN URL   : http://{lan_ip}:{PORT}")

    print("\n ACCESS TOKEN:")
    print(TOKEN)

    print("\n" + "-" * 58)
    print(" STATUS: RUNNING")
    print(" PROTOCOL: WebRTC / VP8")
    print(" DEFAULT: 720p / 20 FPS")
    print(" MAX VIEWERS: 1")
    print("-" * 58)

    print("\nKeep this terminal running.")
    print("Press CTRL+C to stop the server.\n")

    try:

        await asyncio.Event().wait()

    finally:

        await runner.cleanup()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(main())

    except KeyboardInterrupt:

        print("\nHANA DESKTOP STREAM stopped.")


py -3.13 hana_desktop_stream.py

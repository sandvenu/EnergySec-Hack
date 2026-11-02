"""Record the demo page's unattended D1–D5 run (?autoplay=1) in headless Edge, off-screen.

Captures JPEG frames over the DevTools protocol (~4 fps) with timestamps, plus the page's step
marks, into <out>/frames/ and <out>/capture.json. Nothing on the desktop is recorded.

    python record_demo.py <out_dir> [--url http://127.0.0.1:8700/?autoplay=1] [--timeout 600]
"""

import argparse
import asyncio
import base64
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
import websockets

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PORT = 9333


class CDP:
    def __init__(self, ws):
        self.ws, self.n, self.pending = ws, 0, {}

    async def call(self, method, **params):
        self.n += 1
        mid = self.n
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})


async def record(out: Path, url: str, timeout: float, fps: float, attach: bool = False):
    frames = out / "frames"
    if frames.exists():
        shutil.rmtree(frames)
    frames.mkdir(parents=True)
    edge, profile = None, None
    if not attach:
        try:  # a leftover recorder Edge on this port would be recorded instead of a fresh page
            httpx.get(f"http://127.0.0.1:{PORT}/json", timeout=1)
            raise SystemExit(f"port {PORT} is already in use (a previous recorder's Edge?); stop it first")
        except httpx.HTTPError:
            pass
        profile = tempfile.mkdtemp(prefix="edge-rec-")
        # Open the demo URL directly: navigating from about:blank swaps the page into a new process
        # and detaches the DevTools session.
        edge = subprocess.Popen([EDGE, "--headless=new", f"--remote-debugging-port={PORT}", "--window-size=1920,1080",
                                 "--hide-scrollbars", "--mute-audio", "--no-first-run", f"--user-data-dir={profile}",
                                 url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0, times, i, done, marks = time.time(), [], 0, False, []
    try:
        opened = False
        for _ in range(60):
            try:
                targets = httpx.get(f"http://127.0.0.1:{PORT}/json", timeout=2).json()
                page = next((t for t in targets if t.get("type") == "page" and "127.0.0.1:8700" in t.get("url", "")), None)
                if page:
                    break
                if not opened:  # a managed Edge profile may open its own pages instead of the URL: open it as a new tab
                    httpx.put(f"http://127.0.0.1:{PORT}/json/new?{url}", timeout=5)
                    opened = True
            except Exception:
                pass
            await asyncio.sleep(0.5)
        else:
            raise SystemExit("Edge DevTools endpoint did not come up")
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024) as ws:
            cdp = CDP(ws)
            await cdp.call("Emulation.setDeviceMetricsOverride", width=1920, height=1080, deviceScaleFactor=1, mobile=False)
            last_poll = 0.0
            while time.time() - t0 < timeout and not done:
                tick = time.time()
                shot = await cdp.call("Page.captureScreenshot", format="jpeg", quality=82)
                (frames / f"f{i:05d}.jpg").write_bytes(base64.b64decode(shot["data"]))
                times.append(round(tick - t0, 3))
                i += 1
                if tick - last_poll > 1.0:
                    last_poll = tick
                    r = await cdp.call("Runtime.evaluate", returnByValue=True,
                                       expression="JSON.stringify({done: !!window.__autoplayDone, marks: window.__marks || [], now: performance.now()/1000})")
                    state = json.loads(r["result"]["value"])
                    done = state["done"]
                    # page clock -> recorder clock
                    offset = (time.time() - t0) - state["now"]
                    marks = [{"name": m["name"], "t": round(m["t"] + offset, 3)} for m in state["marks"]]
                await asyncio.sleep(max(0.0, 1.0 / fps - (time.time() - tick)))
    finally:
        (out / "capture.json").write_text(json.dumps({"times": times, "marks": marks, "complete": done,
                                                      "seconds": round(time.time() - t0, 1)}, indent=1))
        print(f"{i} frames, {time.time() - t0:.0f}s, complete={done}, marks={[m['name'] for m in marks]}")
        if edge is not None:  # Edge spawns child processes; stop the whole tree, or the page keeps running
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(edge.pid)], capture_output=True)
            try:
                edge.wait(10)
            except subprocess.TimeoutExpired:
                edge.kill()
            shutil.rmtree(profile, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--url", default="http://127.0.0.1:8700/?autoplay=1")
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--fps", type=float, default=4)
    ap.add_argument("--attach", action="store_true",
                    help=f"use a headless Edge already listening on port {PORT} (some managed Edge installs do not "
                         "open the port when started from Python); the caller stops it afterwards")
    a = ap.parse_args()
    asyncio.run(record(Path(a.out), a.url, a.timeout, a.fps, a.attach))


if __name__ == "__main__":
    main()

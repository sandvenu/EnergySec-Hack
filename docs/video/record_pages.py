"""Record a scripted walk through pages (evidence reports) in a headless Edge that is already listening on PORT.

Each page is opened, held, then scrolled to the bottom at a steady pace, as a viewer would read it.
    python record_pages.py <out_dir> <url> <seconds> [<url> <seconds> ...] [--fps 8]
Writes <out_dir>/frames/ and <out_dir>/capture.json ({"times": [...], "marks": [{"name": "page1", "t": ...}]}).
"""

import argparse
import asyncio
import base64
import json
import shutil
import time
from pathlib import Path

import httpx
import websockets

from record_demo import CDP, PORT


async def walk(out: Path, pages, fps: float):
    frames = out / "frames"
    if frames.exists():
        shutil.rmtree(frames)
    frames.mkdir(parents=True)
    first = pages[0][0]
    httpx.put(f"http://127.0.0.1:{PORT}/json/new?{first}", timeout=5)
    for _ in range(40):
        targets = httpx.get(f"http://127.0.0.1:{PORT}/json", timeout=2).json()
        page = next((t for t in targets if t.get("type") == "page" and "127.0.0.1:8700" in t.get("url", "")), None)
        if page:
            break
        await asyncio.sleep(0.5)
    times, marks, i, t0 = [], [], 0, time.time()
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024) as ws:
        cdp = CDP(ws)
        await cdp.call("Emulation.setDeviceMetricsOverride", width=1920, height=1080, deviceScaleFactor=1, mobile=False)
        for n, (url, secs) in enumerate(pages, 1):
            if n > 1:
                await cdp.call("Page.navigate", url=url)
            await asyncio.sleep(2.0)  # load before the clock for this page starts
            r = await cdp.call("Runtime.evaluate", returnByValue=True,
                               expression="document.documentElement.scrollHeight - innerHeight")
            span = max(0, r["result"]["value"])
            marks.append({"name": f"page{n}", "t": round(time.time() - t0, 3)})
            start, hold = time.time(), min(3.0, secs / 4)
            while time.time() - start < secs:
                tick = time.time()
                el = tick - start
                y = 0 if el < hold else span * min(1.0, (el - hold) / max(0.1, secs - hold - 1.5))
                await cdp.call("Runtime.evaluate", expression=f"window.scrollTo(0, {y:.0f})")
                shot = await cdp.call("Page.captureScreenshot", format="jpeg", quality=82)
                (frames / f"f{i:05d}.jpg").write_bytes(base64.b64decode(shot["data"]))
                times.append(round(tick - t0, 3))
                i += 1
                await asyncio.sleep(max(0.0, 1.0 / fps - (time.time() - tick)))
    (out / "capture.json").write_text(json.dumps({"times": times, "marks": marks}, indent=1))
    print(f"{i} frames, marks {marks}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("pages", nargs="+", help="url seconds url seconds ...")
    ap.add_argument("--fps", type=float, default=8)
    a = ap.parse_args()
    pairs = [(a.pages[k], float(a.pages[k + 1])) for k in range(0, len(a.pages), 2)]
    asyncio.run(walk(Path(a.out), pairs, a.fps))


if __name__ == "__main__":
    main()

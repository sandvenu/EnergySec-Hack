"""Assemble the silent 5:00 judging video: deck slides, the recorded AWS demo run, the evidence reports, closing slides.

    python make_video_v3.py <run_recording_dir> <pages_recording_dir> <slides_png_dir> [--out Trusted_rApp_demo_v3.mp4]

<run_recording_dir> is record_demo.py's output for the run (frames/, capture.json); <pages_recording_dir> is
record_pages.py's output (decision log, D1 receipt, D6 incident report). <slides_png_dir> holds the deck exported by
PowerPoint as Slide1.PNG ... SlideN.PNG. No audio track: the voice-over is recorded separately against
VIDEO_TRANSCRIPT_v3.md, which follows TIMELINE below.
"""

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

FFMPEG = shutil.which("ffmpeg") or (r"C:\Users\svenugopal\AppData\Local\Microsoft\WinGet\Packages"
                                    r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe")
RUN_ID, COMMIT = "2026-11-02T13-10-track3-d1d6-04", "f7c196f"
W, H, FPS, TOTAL = 1920, 1080, 25, 300.0

# Run 04 on the recorder's clock: recorder start 13:11:53.3 UTC; the run (ledger seq 75-109) ran
# 13:12:02.9-13:13:48.3, i.e. t = 9.6-115.0 s; D3 began at 13:13:34.4 (t = 101.1 s).
# ("slide", n, seconds) or ("clip", "run"|"pages", src_from, src_to, speed, label)
TIMELINE = [("slide", 1, 10), ("slide", 2, 20), ("slide", 3, 20), ("slide", 4, 20), ("slide", 5, 20),
            ("slide", 6, 15), ("slide", 7, 15),
            ("clip", "run", 7.6, 101.1, 2.0, "D1–D2  ·  2× speed"),
            ("clip", "run", 101.1, 119.0, 1.0, "D3–D6  ·  real time"),
            ("clip", "pages", 2.2, 14.2, 1.0, "decision log after the run"),
            ("clip", "pages", 16.93, 30.9, 1.0, "D1 signed decision receipt"),
            ("clip", "pages", 33.6, 51.5, 1.0, "D6 signed incident report"),
            ("slide", 8, 14), ("slide", 10, 13), ("slide", 11, 26), ("slide", 12, 10), ("slide", 14, None)]


def _nearest(times, x):
    lo, hi = 0, len(times) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if times[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return lo if lo == 0 or abs(times[lo] - x) < abs(times[lo - 1] - x) else lo - 1


def clip_entries(rec: Path, a: float, b: float, speed: float):
    times = json.loads((rec / "capture.json").read_text())["times"]
    n = round((b - a) / speed * FPS)
    return [(rec / "frames" / f"f{_nearest(times, a + k / FPS * speed):05d}.jpg", 1 / FPS) for k in range(n)], n / FPS


def plan():
    """Rows of (start, end, what) for the transcript and the burned-in banner."""
    rows, t = [], 0.0
    fixed = sum(e[2] if e[0] == "slide" else (e[3] - e[2]) / e[4] for e in TIMELINE if not (e[0] == "slide" and e[2] is None))
    for e in TIMELINE:
        d = (TOTAL - fixed) if e[0] == "slide" and e[2] is None else (e[2] if e[0] == "slide" else (e[3] - e[2]) / e[4])
        rows.append((t, t + d, e))
        t += d
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("pages")
    ap.add_argument("slides")
    ap.add_argument("--out", default=str(Path(__file__).with_name("Trusted_rApp_demo_v3.mp4")))
    a = ap.parse_args()
    recs = {"run": Path(a.run), "pages": Path(a.pages)}
    work = Path(tempfile.mkdtemp(prefix="video-v3-"))
    entries, demo = [], []
    for start, end, e in plan():
        if e[0] == "slide":
            img = work / f"slide{e[1]}.jpg"  # JPEG like the frames: the concat demuxer keeps the first file's decoder
            if not img.exists():
                Image.open(Path(a.slides) / f"Slide{e[1]}.PNG").convert("RGB").resize((W, H), Image.LANCZOS).save(
                    img, quality=95)
            entries.append((img, end - start))
        else:
            ent, _ = clip_entries(recs[e[1]], e[2], e[3], e[4])
            entries += ent
            demo.append((start, end, e[5]))
    merged = []  # consecutive repeats of one frame become one entry
    for f, dur in entries:
        if merged and merged[-1][0] == f:
            merged[-1][1] += dur
        else:
            merged.append([f, dur])
    lst = work / "list.txt"
    lst.write_text("".join(f"file '{f.as_posix()}'\nduration {dur:.4f}\n" for f, dur in merged)
                   + f"file '{merged[-1][0].as_posix()}'\n", encoding="utf-8")
    (work / "banner.txt").write_text(f"LIVE ON AWS us-east-2  ·  team07  ·  run {RUN_ID}  ·  commit {COMMIT}",
                                     encoding="utf-8")
    d0, d1 = demo[0][0], demo[-1][1]
    on = f"enable='between(t,{d0:.2f},{d1:.2f})'"
    font = "fontfile='C\\:/Windows/Fonts/arialbd.ttf'"
    bt = (work / "banner.txt").as_posix().replace(":", "\\:")
    labels = ""
    for k, (s, e, label) in enumerate(demo):  # what this segment is, and its speed, at the banner's right end
        lf = work / f"label{k}.txt"
        lf.write_text(label, encoding="utf-8")
        labels += (f",drawtext={font}:textfile='{lf.as_posix().replace(':', chr(92) + ':')}':fontsize=24:"
                   f"fontcolor=0xFFD54F:x=w-tw-30:y=h-38:enable='between(t,{s:.2f},{e:.2f})'")
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:white,fps={FPS},"
          f"drawbox=x=0:y=ih-52:w=iw:h=52:color=0x1F1F2E@0.9:t=fill:{on},"
          f"drawtext={font}:textfile='{bt}':fontsize=26:fontcolor=white:x=30:y=h-39:{on}{labels},format=yuv420p")
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-vf", vf,
                    "-t", f"{TOTAL:.2f}", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                    "-movflags", "+faststart", "-an", a.out], check=True)
    shutil.rmtree(work, ignore_errors=True)
    for start, end, e in plan():
        print(f"{int(start // 60)}:{start % 60:05.2f}-{int(end // 60)}:{end % 60:05.2f}  "
              f"{'slide ' + str(e[1]) if e[0] == 'slide' else e[5]}")
    print("saved", a.out)


if __name__ == "__main__":
    main()

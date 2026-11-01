"""Assemble the pitch video: slide images + recorded demo + narration (Windows text-to-speech).

    python make_video.py --slides <dir of Slide1.PNG ...> --capture <recording dir> --out <video.mp4>
                         [--script script.json] [--work <scratch dir>] [--audio-dir <recorded voice WAVs>]

Each segment's length follows its narration. A slide segment shows the slide; a demo segment shows
the recorded frames between that step's marks, sped up to fit the narration (labelled when faster
than 1.3x). With --audio-dir, <segment id>.wav files there replace the synthetic voice (your own
recording). Writes the MP4, an .srt with the narration, and timings.json.
"""

import argparse
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
FONT = "C\\:/Windows/Fonts/segoeuib.ttf"
W, H, FPS = 1920, 1080, 30
PAD = 0.7  # seconds of silence after each narration segment


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(map(str, cmd))[:300]}\n{r.stderr[-2000:]}")
    return r.stdout


def duration(path: Path) -> float:
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]).strip())


def tts(text: str, wav: Path, voice: str, rate: int):
    ps = (
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"$s.SelectVoice('{voice}'); $s.Rate = {rate};"
        f"$s.SetOutputToWaveFile('{wav}');"
        "$t = [Console]::In.ReadToEnd(); $s.Speak($t); $s.Dispose()"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], input=text, capture_output=True, text=True)
    if r.returncode != 0 or not wav.exists():
        raise SystemExit(f"text-to-speech failed for {wav.name}: {r.stderr[-500:]}")


def slide_clip(img: Path, seconds: float, out: Path):
    fade = min(0.4, seconds / 4)
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0F1B2D,"
          f"fps={FPS},format=yuv420p,fade=t=in:st=0:d={fade},fade=t=out:st={seconds - fade:.3f}:d={fade}")
    run(["ffmpeg", "-y", "-loop", "1", "-i", str(img), "-t", f"{seconds:.3f}", "-vf", vf,
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-an", str(out)])


RESULT_SHARE = 0.55  # of a demo segment's narration time spent on the step's result, at real speed


def demo_clip(capture: Path, t_start: float, t_done: float, t_end: float, seconds: float, out: Path, work: Path,
              label: str):
    """Agent working time [t_start, t_done) is compressed; the result [t_done, t_end] plays at real speed
    and is held, so the result is on screen while the narration describes it."""
    cap = json.loads((capture / "capture.json").read_text())
    times = cap["times"]
    idx = [i for i, t in enumerate(times) if t_start <= t <= t_end]
    if len(idx) < 2:
        raise SystemExit(f"no frames between {t_start} and {t_end}")
    real_a, real_b = max(t_done - times[idx[0]], 0.0), max(times[idx[-1]] - t_done, 0.0)
    speed_a = max(real_a / (seconds * (1 - RESULT_SHARE)), 1.0)
    dur_a = real_a / speed_a
    speed_b = max(real_b / max(seconds - dur_a, 0.1), 1.0)
    # concat list with each frame held until the next one, compressed by its part's speed
    lst = work / f"{out.stem}.txt"
    lines = []
    for a, b in zip(idx, idx[1:] + [idx[-1]]):
        speed = speed_a if times[a] < t_done else speed_b
        d = max((times[b] - times[a]) / speed, 1.0 / FPS) if b != a else 0.5
        lines.append(f"file '{(capture / 'frames' / f'f{a:05d}.jpg').as_posix()}'\nduration {d:.4f}")
    lines.append(f"file '{(capture / 'frames' / f'f{idx[-1]:05d}.jpg').as_posix()}'")
    lst.write_text("\n".join(lines))
    # hold the last frame when the recording is shorter than the narration, or the voice drifts out of sync
    vf = f"fps={FPS},scale={W}:{H},tpad=stop_mode=clone:stop_duration={seconds:.3f},format=yuv420p"
    if speed_a > 1.3:
        tag = f"{label}  ·  live agent run, sped up {speed_a:.0f}x" if speed_a >= 2 else f"{label}  ·  live agent run, sped up"
        vf += (f",drawtext=fontfile='{FONT}':text='{tag}':x=w-tw-36:y=h-th-40:fontsize=28:fontcolor=white:"
               f"box=1:boxcolor=0x0F1B2DCC:boxborderw=14:enable='lt(t,{dur_a:.3f})'")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-t", f"{seconds:.3f}", "-vf", vf,
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-an", str(out)])
    return speed_a, real_a + real_b


def demo_windows(capture: Path):
    """Chapter windows from the page's marks: D1 = [D1:start, D1:done, D2:start) ... D5 = [.., end]."""
    marks = {m["name"]: m["t"] for m in json.loads((capture / "capture.json").read_text())["marks"]}
    order = ["D1", "D2", "D3", "D4", "D5"]
    win = {}
    for i, d in enumerate(order):
        start, done = marks[f"{d}:start"], marks[f"{d}:done"]
        end = marks[f"{order[i + 1]}:start"] if i + 1 < len(order) else marks.get("end", done + 5)
        win[d] = (start, done, end)
    return win


def srt_time(t: float) -> str:
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s - int(s)) * 1000)):03d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slides", required=True)
    ap.add_argument("--capture", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--script", default=str(HERE / "script.json"))
    ap.add_argument("--work", default=None)
    ap.add_argument("--audio-dir", default=None)
    a = ap.parse_args()

    script = json.loads(Path(a.script).read_text(encoding="utf-8"))
    slides, capture, out = Path(a.slides), Path(a.capture), Path(a.out)
    work = Path(a.work) if a.work else out.parent / "video_work"
    work.mkdir(parents=True, exist_ok=True)
    windows = demo_windows(capture)

    parts, timeline, t = [], [], 0.0
    for seg in script["segments"]:
        sid, text = seg["id"], seg["text"].strip()
        if not text:
            raise SystemExit(f"segment {sid} has no narration yet")
        wav = work / f"{sid}.wav"
        own = Path(a.audio_dir) / f"{sid}.wav" if a.audio_dir else None
        if own and own.exists():
            wav = own
        else:
            tts(text, wav, script["voice"], script["rate"])
        speech = duration(wav)
        seconds = speech + PAD
        clip = work / f"{sid}.mp4"
        info = {"id": sid, "start": round(t, 2), "seconds": round(seconds, 2), "speech": round(speech, 2)}
        if "slide" in seg:
            img = next((p for p in slides.iterdir() if p.stem.lower() == f"slide{seg['slide']}"), None)
            if img is None:
                raise SystemExit(f"no image for slide {seg['slide']} in {slides}")
            slide_clip(img, seconds, clip)
            info["slide"] = seg["slide"]
        else:
            s0, sd, s1 = windows[seg["demo"]]
            speed, real = demo_clip(capture, s0, sd, s1, seconds, clip, work, seg["demo"])
            info.update({"demo": seg["demo"], "real_seconds": round(real, 1), "speed": round(speed, 2)})
        # mux this segment's voice (padded) onto its picture
        seg_av = work / f"{sid}_av.mp4"
        run(["ffmpeg", "-y", "-i", str(clip), "-i", str(wav), "-filter_complex",
             f"[1:a]aresample=48000,apad=whole_dur={seconds:.3f}[a]", "-map", "0:v", "-map", "[a]",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-t", f"{seconds:.3f}", str(seg_av)])
        parts.append(seg_av)
        timeline.append({**info, "text": text})
        t += seconds
        print(f"{sid:10s} {seconds:5.1f}s " + (f"slide {seg['slide']}" if "slide" in seg else
                                               f"demo {seg['demo']} ({info['real_seconds']}s real, x{info['speed']})"))

    lst = work / "parts.txt"
    lst.write_text("\n".join(f"file '{p.as_posix()}'" for p in parts))
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(out)])
    srt = out.with_suffix(".srt")
    srt.write_text("\n".join(f"{i}\n{srt_time(x['start'])} --> {srt_time(x['start'] + x['speech'])}\n{x['text']}\n"
                             for i, x in enumerate(timeline, 1)), encoding="utf-8")
    (out.parent / f"{out.stem}_timings.json").write_text(json.dumps(timeline, indent=1), encoding="utf-8")
    print(f"done: {out} ({t:.1f}s), {srt.name}")


if __name__ == "__main__":
    main()

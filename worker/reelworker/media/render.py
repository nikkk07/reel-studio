"""Parallel chunk render -> concat -> loudness-normalised mux -> archive master + delivery file (<= size cap)."""
import json, os, re, subprocess, sys
from pathlib import Path
from .gfx_base import FPS


def _run(cmd, timeout=1800, **kw):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"{Path(cmd[0]).name} took longer than {timeout // 60} min and was stopped") from e
    if r.returncode != 0:
        raise RuntimeError(f"{Path(cmd[0]).name} failed: {(r.stderr or r.stdout)[-800:]}")
    return r


def _procs(n_cfg: int) -> int:
    if n_cfg > 0: return n_cfg
    return max(1, min(6, (os.cpu_count() or 2) // 2))


def render_video(tl_path: Path, out_dir: Path, end: float, procs_cfg: int, progress=lambda f: None) -> Path:
    nf = int(round(end * FPS)); n = _procs(procs_cfg); step = (nf + n - 1) // n
    parts, jobs = [], []
    for i in range(n):
        f0, f1 = i * step, min(nf, (i + 1) * step)
        if f0 >= f1: break
        out = out_dir / f"part_{i:02d}.mp4"; parts.append(out)
        logf = out_dir / f"part_{i:02d}.log"
        with open(logf, "wb") as fh:
            jobs.append((subprocess.Popen([sys.executable, "-m", "reelworker.media.compose", str(tl_path), str(f0), str(f1), str(out)],
                                          cwd=Path(__file__).resolve().parents[2], stdout=fh, stderr=fh), logf))
    for k, (p, logf) in enumerate(jobs):
        try:
            p.wait(timeout=3600)
        except subprocess.TimeoutExpired:
            for q, _ in jobs: q.kill()
            raise RuntimeError("drawing the frames took longer than 60 min and was stopped")
        if p.returncode != 0:
            raise RuntimeError(f"render chunk {k} failed: {logf.read_text(errors='ignore')[-800:]}")
        progress((k + 1) / len(jobs))
    lst = out_dir / "parts.txt"; lst.write_text("".join(f"file '{p.name}'\n" for p in parts))
    full = out_dir / "video_full.mp4"
    _run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(full)])
    for p in parts: p.unlink(missing_ok=True)
    return full


def loudness(path) -> dict:
    r = subprocess.run(["ffmpeg", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True)
    s = r.stderr[r.stderr.rfind("Summary:"):]
    I = re.search(r"I:\s+(-?[\d.]+) LUFS", s); P = re.search(r"Peak:\s+(-?[\d.]+) dBFS", s)
    return {"lufs": float(I.group(1)) if I else None, "true_peak": float(P.group(1)) if P else None}


def master(video: Path, mix_wav: Path, out_dir: Path, max_mb: float) -> dict:
    lim = out_dir / "mix_lim.wav"
    _run(["ffmpeg", "-v", "error", "-y", "-i", str(mix_wav), "-af", "alimiter=limit=0.6:attack=1:release=50:level=false", str(lim)])
    r = subprocess.run(["ffmpeg", "-nostats", "-i", str(lim), "-af", "loudnorm=I=-14:TP=-2:LRA=11:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    # pick the loudnorm JSON block itself; newer ffmpeg prints more lines after it
    blocks = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr)
    if not blocks:
        raise RuntimeError(f"loudness measurement failed: {r.stderr[-400:]}")
    d = json.loads(blocks[-1])
    ln = (f"loudnorm=I=-14:TP=-2:LRA=11:measured_I={d['input_i']}:measured_TP={d['input_tp']}:measured_LRA={d['input_lra']}:"
          f"measured_thresh={d['input_thresh']}:offset={d['target_offset']}:linear=true,aresample=48000")
    arch = out_dir / "master_archive.mp4"
    _run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", str(lim), "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-af", ln,
          "-c:a", "aac", "-b:a", "320k", "-movflags", "+faststart", "-shortest", str(arch)])
    dur = float(_run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(arch)]).stdout.strip())
    deliv = out_dir / "reel.mp4"; target = max_mb
    for attempt in range(3):
        vbr = max(2.5e6, min(9e6, (target * 8e6 / dur) - 330e3) * 0.94)
        _run(["ffmpeg", "-v", "error", "-y", "-i", str(arch), "-c:v", "libx264", "-preset", "medium", "-b:v", str(int(vbr)),
              "-maxrate", str(int(vbr * 1.25)), "-bufsize", str(int(vbr * 2)), "-pix_fmt", "yuv420p", "-profile:v", "high",
              "-c:a", "copy", "-movflags", "+faststart", str(deliv)])
        if deliv.stat().st_size <= max_mb * 1e6: break
        target *= 0.85
    thumb = out_dir / "thumb.jpg"
    _run(["ffmpeg", "-v", "error", "-y", "-ss", "1.0", "-i", str(deliv), "-frames:v", "1", "-vf", "scale=540:960", str(thumb)])
    return {"archive": str(arch), "delivery": str(deliv), "thumb": str(thumb), "duration": round(dur, 2),
            "delivery_mb": round(deliv.stat().st_size / 1e6, 1), **loudness(deliv)}


def frames_sheet(video: Path, out: Path, fontfile: str, every=1.0):
    """One frame per second, time-stamped, tiled 10 per row. Uses Pillow for the labels, so it needs no
    optional ffmpeg filters (Homebrew's ffmpeg ships without drawtext)."""
    from PIL import Image, ImageDraw, ImageFont
    tmp = out.parent / "qcframes"; tmp.mkdir(exist_ok=True)
    for f in tmp.glob("*.jpg"): f.unlink()
    _run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf", f"fps={1/every},scale=216:384", str(tmp / "q_%03d.jpg")])
    frames = sorted(tmp.glob("q_*.jpg"))
    if not frames:
        raise RuntimeError("could not extract frames for the quality check")
    try:
        font = ImageFont.truetype(fontfile, 16)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    cols = 10; rows = (len(frames) + cols - 1) // cols
    sheet = Image.new("RGB", (216 * cols, 384 * rows), (0, 0, 0)); d = ImageDraw.Draw(sheet)
    for k, f in enumerate(frames):
        x, y = (k % cols) * 216, (k // cols) * 384
        sheet.paste(Image.open(f).convert("RGB"), (x, y))
        t = k * every; label = f"{int(t // 60):02d}:{int(t % 60):02d}"
        d.rectangle([x + 2, y + 2, x + 62, y + 24], fill=(0, 0, 0)); d.text((x + 6, y + 4), label, fill=(255, 214, 0), font=font)
    sheet.save(out, quality=85)
    return str(out)

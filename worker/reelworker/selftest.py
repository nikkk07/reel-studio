"""Health check of the Mac install. Prints PASS/FAIL lines, never secrets.

    python -m reelworker.selftest            # files, tools, Supabase, Claude
    python -m reelworker.selftest --voice    # + one short narration take (downloads models on first run)
    python -m reelworker.selftest --render   # + 3-second test render with every graphic component
"""
import json, shutil, subprocess, sys, tempfile, time
from pathlib import Path
from . import config

results = []
def check(name, fn):
    t0 = time.time()
    try:
        note = fn() or ""
        results.append(True); print(f"PASS  {name}  {note}  ({time.time() - t0:.1f}s)", flush=True)
    except Exception as e:  # noqa: BLE001
        results.append(False); print(f"FAIL  {name}  {str(e)[:300]}", flush=True)


def files():
    need = [config.VOICE_DIR / "refs/female.wav", config.VOICE_DIR / "refs/roomtone.wav", config.LEXICON, config.LOGO, config.UTMOS_CKPT]
    missing = [str(p) for p in need if not p.exists()]
    if missing: raise RuntimeError("missing: " + ", ".join(missing))
    from .media.gfx_base import load_fonts
    load_fonts(str(config.FONTS_DIR)); return f"root {config.ROOT}"

def tools():
    for t in ("ffmpeg", "ffprobe"):
        if not shutil.which(t): raise RuntimeError(f"{t} not found on PATH")
    enc = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    if "loudnorm" not in subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout:
        raise RuntimeError("ffmpeg without loudnorm filter")
    return "h264_videotoolbox" if "h264_videotoolbox" in enc else "libx264 (no hardware encoder)"

def env_keys():
    miss = [k for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "PEXELS_API_KEY") if not config.get(k)]
    if miss: raise RuntimeError(f"not set in {config.ROOT / '.env'}: {', '.join(miss)}")
    return "keys present" + ("" if config.get("PIXABAY_API_KEY") else " (Pixabay optional key missing)")

def supabase():
    from .db import Supabase
    db = Supabase(); db.select("workers", "select=id&limit=1"); db.list_objects("")
    return "database + storage reachable"

def claude():
    from . import brain
    ok, note = brain.health_check()
    if not ok: raise RuntimeError(note)
    return note

def footage():
    from .media import footage as f
    r = f.search("airplane takeoff", per_source=2)
    if not r: raise RuntimeError("no results (check PEXELS_API_KEY / network)")
    return f"{len(r)} clips found"

def voice():
    d = Path(tempfile.mkdtemp(prefix="rs_selftest_"))
    say = "नमस्ते, ये We One Aviation का voice test है।"
    from .media import lexicon
    txt = lexicon.apply(say, lexicon.load(str(config.LEXICON)))
    (d / "voice_in.json").write_text(json.dumps({"groups": [{"id": "T1", "text": txt, "phrases_say": [txt]}], "seeds": [11],
        "ref": str(config.VOICE_DIR / "refs/female.wav"), "device": config.DEVICE, "utmos_ckpt": str(config.UTMOS_CKPT),
        "whisper": config.get("REELSTUDIO_WHISPER", "medium"), "threads": 4}, ensure_ascii=False))
    subprocess.run([sys.executable, "-m", "reelworker.media.voice", str(d)], check=True, cwd=Path(__file__).resolve().parents[1])
    out = json.loads((d / "voice_out.json").read_text())["T1"]["chosen"]
    return f"clarity {out['word_prob']}, naturalness {out['utmos']}, heard: {out['transcript'][:60]}  → {d}/vo"

def render():
    from .media import compose
    d = Path(tempfile.mkdtemp(prefix="rs_render_"))
    src = d / "bg.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1296x2304:r=60:d=4", "-pix_fmt", "yuv420p", str(src)], check=True)
    g = lambda t, a, b, **p: {"type": t, "t0": a, "t1": b, "params": p}
    TL = {"groups": [], "captions": [{"text": "Test *caption* line", "t0": 0.2, "t1": 2.8}], "shots": [{"id": "S1", "t0": 0, "t1": 3.0, "z0": 1, "z1": 1.1, "need": 3.4, "file": str(src)}],
          "graphics": [g("chip", 0.1, 2.9, text="SELF TEST", color="amber"), g("counter", 0.2, 2.9, to=90, suffix=" SEC", label="test"), g("flash", 0.0, 0.6, bolt=True),
                       g("plane_path", 1.0, 2.9), g("timer", 0.3, 2.9, seconds=90, label="TEST")],
          "vo_end": 2.0, "end": 3.0, "note_range": [0, 0], "fonts_dir": str(config.FONTS_DIR), "logo": str(config.LOGO), "mood": "curious"}
    tl = d / "timeline.json"; tl.write_text(json.dumps(TL))
    subprocess.run([sys.executable, "-m", "reelworker.media.compose", str(tl), "0", "180", str(d / "out.mp4")], check=True, cwd=Path(__file__).resolve().parents[1])
    # the rest of the real pipeline: mastering (loudness), delivery encode, thumbnail, QC contact sheet
    from .media import render
    import numpy as np, soundfile as sf
    sr = 48000; t = np.arange(sr * 3) / sr
    sf.write(d / "mix.wav", np.stack([0.2 * np.sin(2 * np.pi * 220 * t)] * 2, 1), sr)
    res = render.master(d / "out.mp4", d / "mix.wav", d, 45)
    render.frames_sheet(Path(res["delivery"]), d / "frames.jpg", "")
    return f"{res['lufs']} LUFS, {res['delivery_mb']} MB  → {d}"


if __name__ == "__main__":
    print(f"Reel Studio worker {config.VERSION} self-test")
    check("files & fonts", files); check("ffmpeg", tools); check(".env keys", env_keys); check("Supabase", supabase)
    check("Claude Code (Pro login)", claude); check("stock footage search", footage)
    if "--render" in sys.argv: check("test render", render)
    if "--voice" in sys.argv: check("voice model", voice)
    print("ALL PASS" if all(results) else "SOME CHECKS FAILED"); sys.exit(0 if all(results) else 1)

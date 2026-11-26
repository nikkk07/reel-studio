"""Dialogue reels: two or three AI characters talking to each other (shot / reverse-shot), made with free tools only.

    voice   Gemini TTS (one call per line, the character's own prebuilt voice and delivery style; API keys from the Mac's
            Keychain, service "reelstudio-gemini", accounts key1, key2, key3...; the next key / model is tried on a refusal)
    faces   InfiniteTalk on the two Kaggle kernels (presenter.py): only the lines marked "lip" are lip-synced; the other
            lines play over a still of the speaker or a listener (slow push-in), or over stock cutaways
    edit    the classic compose renderer: one full-screen shot at a time, whip cuts, captions with *keywords*, graphics,
            AI label, end card, mix (voice + SFX + music bed + room tone), loudness master

Everything a stage makes is kept in the work folder, so a stopped run carries on where it stopped (a Kaggle render that is
still running is picked up again, never sent twice). The spec format is documented in scripts/dialogue_reel.py.
"""
import base64, json, subprocess, time, urllib.error, urllib.request
from pathlib import Path

import numpy as np, soundfile as sf

from . import presenter
from .gfx_base import W, H, FPS

TTS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
TTS_MODELS = ("gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts", "gemini-3.1-flash-tts-preview")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
LEAD = 0.08                      # a cut lands this much before the line's first word (feels like a real edit)


def _run(cmd, timeout=900):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError(f"{cmd[0]} failed: {(r.stderr or r.stdout)[-400:]}")
    return r


# ------------------------------------------------------------------ keys (never printed, never written to disk)
def keys(max_keys=6):
    out = []
    for i in range(1, max_keys + 1):
        r = subprocess.run(["security", "find-generic-password", "-s", "reelstudio-gemini", "-a", f"key{i}", "-w"],
                           capture_output=True, text=True)
        k = r.stdout.strip()
        if r.returncode == 0 and k:
            out.append(k)
    return out


# ------------------------------------------------------------------ images
def find_image(folder: Path, name: str):
    for ext in IMAGE_EXT:
        for p in (folder / f"{name}{ext}", folder / f"{name}{ext.upper()}"):
            if p.exists():
                return p
    return None


def frame_916(src: Path, out: Path) -> Path:
    """Any picture -> 1080x1920: centre crop to 9:16 (ChatGPT's 2:3 loses a little at the sides), high-quality scale."""
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf",
          f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H}", "-frames:v", "1", str(out)])
    return out


def still_clip(img: Path, out: Path, dur: float, gray=False) -> Path:
    """A still as a 60 fps clip (the renderer reads clips by frame number); the push-in is the renderer's own zoom."""
    vf = f"fps={FPS},format=yuv420p" + (",hue=s=0.15,eq=contrast=1.08" if gray else "")
    _run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(img), "-t", f"{dur + 0.5:.2f}", "-vf", vf,
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", str(out)])
    return out


def video_clip(src: Path, out: Path, dur: float, off=0.0) -> Path:
    """Stock or lip-sync video -> 1080x1920 60 fps, covering the frame; holds the last frame when the clip is short."""
    vf = (f"fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},"
          f"tpad=stop_mode=clone:stop_duration={dur + 1:.2f},format=yuv420p")
    _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{off:.2f}", "-i", str(src), "-vf", vf, "-t", f"{dur + 0.5:.2f}", "-an",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", str(out)])
    return out


# ------------------------------------------------------------------ voice
def tts(text: str, voice: str, style: str, api_keys: list, out: Path, log=print) -> Path:
    """One line through Gemini TTS -> WAV. Tries every key with every model before giving up."""
    body = {"input": [{"type": "user_input", "content": [{"type": "text", "text": text,
                                                         "annotations": [{"type": "speech_metadata", "style": style}] if style else []}]}],
            "response_format": {"type": "audio"}, "generation_config": {"speech_config": [{"voice": voice}]}}
    last = ""
    for model in TTS_MODELS:
        for n, k in enumerate(api_keys, 1):
            req = urllib.request.Request(TTS_URL, data=json.dumps({"model": model, **body}).encode(),
                                         headers={"x-goog-api-key": k, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    j = json.loads(r.read())
            except urllib.error.HTTPError as e:
                last = f"{model} key{n}: HTTP {e.code} {e.read()[:200].decode(errors='ignore')}"
                time.sleep(2 if e.code == 429 else 0)
                continue
            except Exception as e:  # noqa: BLE001
                last = f"{model} key{n}: {type(e).__name__}"
                continue
            data = None
            for st in j.get("steps") or []:
                if st.get("type") == "model_output":
                    for c in st.get("content") or []:
                        if c.get("type") == "audio" and c.get("data"):
                            data = c["data"]
            if not data:
                last = f"{model} key{n}: no audio in the answer"
                continue
            raw = base64.b64decode(data); out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".raw.wav")
            if raw[:4] == b"RIFF":
                tmp.write_bytes(raw)
            else:                                        # headerless 24 kHz 16-bit mono PCM
                sf.write(str(tmp), np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768, 24000)
            _clean(tmp, out); tmp.unlink(missing_ok=True)
            log(f"voice {out.stem}: {voice} via {model} (key{n})")
            return out
    raise RuntimeError(f"Gemini TTS refused every key and model ({last})")


def _clean(src: Path, out: Path):
    """48 kHz mono, silence trimmed at both ends (a line starts and ends on its words), short fades."""
    af = ("aresample=48000,aformat=channel_layouts=mono,"
          "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.03,areverse,"
          "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.06,areverse,"
          "afade=t=in:d=0.01")
    _run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af", af, "-c:a", "pcm_s16le", str(out)])


def seconds(p: Path) -> float:
    i = sf.info(str(p)); return i.frames / i.samplerate


# ------------------------------------------------------------------ timeline
def plan(spec: dict, durs: dict) -> dict:
    """Line times and the picture list. Each line's picture runs from just before its first word until the next picture.
    beats (spec "beat" on a line): a silent hold after the line on another picture (the freeze before the twist)."""
    t = float(spec.get("start", 0.2)); lines = []; pics = []
    for ln in spec["lines"]:
        d = durs[ln["id"]]; t0 = t; t1 = t0 + d
        lines.append({**ln, "t0": round(t0, 3), "t1": round(t1, 3)})
        segs = ln.get("pics") or [{"img": ln["shot"], "lip": ln.get("lip", False), "whip": ln.get("whip", False)}]
        # pictures inside the line: {"img"|"stock", "at": seconds from the line start (first one 0)}
        for k, s in enumerate(segs):
            if s.get("at") == "end":                 # from the line's last word (an end card's background)
                p0 = t1
            else:
                p0 = t0 + float(s.get("at", 0.0)) - (LEAD if k == 0 else 0.0)
                if k and p0 >= t1 - 0.2:             # the line is shorter than planned: this picture would not show
                    continue
            pics.append({**s, "line": ln["id"], "t0": round(max(0.0, p0), 3)})
        t = t1 + float(ln.get("pause", 0.28))
        if ln.get("beat"):
            b = ln["beat"]; pics.append({**b, "line": ln["id"], "t0": round(t1 + 0.12, 3)}); t = t1 + 0.12 + float(b.get("dur", 1.2))
    vo_end = lines[-1]["t1"] + 0.25
    end = vo_end + float(spec.get("end_hold", 3.2))
    pics[0]["t0"] = 0.0
    for i, p in enumerate(pics):
        p["t1"] = round(pics[i + 1]["t0"] if i + 1 < len(pics) else end, 3)
    return {"lines": lines, "pics": pics, "vo_end": round(vo_end, 3), "end": round(end, 3)}


def captions(lines: list) -> list:
    """Captions: the line's own on-screen text, split into chunks of up to 5 words timed by length across the line."""
    out = []
    for ln in lines:
        words = str(ln.get("cap") or "").split()
        if not words:
            continue
        chunks, cur = [], []
        for w in words:
            cur.append(w)
            if len(cur) >= 5 or w.endswith((".", "?", "!", "…")) and len(cur) >= 2:
                chunks.append(cur); cur = []
        if cur:
            chunks.append(cur)
        total = sum(len(" ".join(c)) for c in chunks); t = ln["t0"]; span = ln["t1"] - ln["t0"]
        for c in chunks:
            d = span * len(" ".join(c)) / max(1, total)
            out.append({"t0": round(t, 3), "t1": round(t + d, 3), "text": " ".join(c)}); t += d
    return out


def graphics(spec: dict, P: dict) -> list:
    """Spec graphics are placed on a line: {"type", "line", "at" (s after the line starts), "dur", "params"}."""
    at = {ln["id"]: ln for ln in P["lines"]}; out = []
    for g in spec.get("graphics", []):
        ln = at[g["line"]]
        t0 = ln["t1"] + 0.15 if g.get("at") == "end" else ln["t0"] + float(g.get("at", 0.0))      # "end": on the silent beat after it
        g2 = {"type": g["type"], "t0": round(t0, 3), "t1": round(t0 + float(g.get("dur", 2.5)), 3), "params": g.get("params", {})}
        if g.get("box"):                         # where it may draw (kept off the characters' faces)
            g2["box"] = [float(x) for x in g["box"]]
        if g.get("reveal_every"):
            n = len(g2["params"].get("items") or []); g2["reveal"] = [round(t0 + i * float(g["reveal_every"]), 3) for i in range(n)]
        out.append(g2)
    return out


# ------------------------------------------------------------------ lip-sync on Kaggle: one batch per kernel, resumable
BATCH_TIMEOUT_S = 11.5 * 3600            # Kaggle stops a GPU session at 12 h; a batch of 3 shots takes about 7 h


def _submit_batch(lane, jobs, log):
    """All of this lane's shots in one kernel run (the weights load once): wavs + pictures + job.json with items."""
    d, _, _ = presenter.LANES[lane]; ds = presenter.KDIR / d / "ds"
    for f in list(ds.glob("*.wav")) + list(ds.glob("shot_*.png")) + list(ds.glob("shot_*.jpg")):
        f.unlink()
    items = []
    for j in jobs:
        img = f"shot_{j['id']}.png"
        presenter.shutil.copy(j["audio"], ds / f"{j['id']}.wav"); presenter.shutil.copy(j["image"], ds / img)
        items.append({"name": j["id"], "image": img, "audio": f"{j['id']}.wav", "prompt": j["prompt"]})
    job = {"prompt": jobs[0]["prompt"], "items": items, "steps": int(presenter.config.get("REELSTUDIO_PRESENTER_STEPS", "8")),
           "teacache": False, "quant_file": "infinitetalk_single_int8_lora", "size": "infinitetalk-480"}
    (ds / "job.json").write_text(json.dumps(job, indent=1), encoding="utf-8")
    presenter._kg("datasets", "version", "-p", str(ds), "-m", f"dialogue {lane} {int(time.time())}", "--dir-mode", "skip")
    time.sleep(presenter.SETTLE_S)
    presenter._kg("kernels", "push", "-p", str(presenter.KDIR / d / "k"))
    log(f"lip-sync: {len(jobs)} shot(s) sent to Kaggle kernel {lane}: {', '.join(j['id'] for j in jobs)}")
    return time.time()


def lipsync(jobs: list, work: Path, log, wait=True) -> dict:
    """jobs = [{"id", "audio", "image", "prompt"}] -> {id: raw mp4}. The shots are split across the two kernels and each
    kernel renders its share in one run, so nothing on this Mac has to stay on while Kaggle works: run the command again
    later (it waits, or with wait=False just checks) and the finished clips are collected. State in work/kaggle.json."""
    state_p = work / "kaggle.json"
    st = json.loads(state_p.read_text()) if state_p.exists() else {}
    save = lambda: state_p.write_text(json.dumps(st, indent=1))  # noqa: E731
    batches = st.setdefault("_batches", {})
    sent = {i for b in batches.values() for i in b["ids"]}
    new = [j for j in jobs if j["id"] not in sent and st.get(j["id"], {}).get("state") not in ("done", "failed")]
    if new:
        free = [l for l in presenter.LANES if batches.get(l, {}).get("state") != "running"]
        for k, lane in enumerate(free):
            share = new[k::len(free)]
            if share:
                t0 = _submit_batch(lane, share, log)
                batches[lane] = {"ids": [j["id"] for j in share], "state": "running", "t0": t0}; save()
    while True:
        for lane, b in batches.items():
            if b["state"] != "running":
                continue
            try:
                k = presenter.status(lane)
            except presenter.Unavailable as e:
                log(f"Kaggle {lane}: status check failed ({str(e)[:120]})"); continue
            if k == "COMPLETE" or k in ("ERROR", "CANCEL") or time.time() - b["t0"] > BATCH_TIMEOUT_S:
                h = min(time.time() - b["t0"], BATCH_TIMEOUT_S) / 3600; presenter.spend(h)
                out = work / f"out_{lane}"; presenter.shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
                try:
                    presenter._kg("kernels", "output", f"{presenter.OWNER}/{presenter.LANES[lane][2]}", "-p", str(out), timeout=1800)
                except Exception as e:  # noqa: BLE001
                    log(f"Kaggle {lane}: download failed ({str(e)[:120]})")
                for sid in b["ids"]:
                    f = out / f"{sid}.mp4"
                    st[sid] = {"lane": lane, "state": "done", "raw": str(f)} if f.exists() else {"lane": lane, "state": "failed", "why": k}
                    log(f"lip-sync {sid}: {'ready' if f.exists() else 'not made (' + k + '); this line plays over its still'}")
                b["state"] = "done"; b["gpu_h"] = round(h, 2); save()
            else:
                log(f"Kaggle {lane}: {k.lower()}, {(time.time() - b['t0']) / 3600:.1f} h so far ({', '.join(b['ids'])})")
        if not any(b["state"] == "running" for b in batches.values()) or not wait:
            break
        time.sleep(presenter.POLL_S)
    return {sid: Path(v["raw"]) for sid, v in st.items() if sid != "_batches" and v.get("state") == "done"}

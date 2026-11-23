"""AI presenter: an AI-generated character speaks parts of the reel with natural head, shoulder and lip movement.

Engine: InfiniteTalk on Kaggle's free GPUs (the only route that looked natural in testing; mouth-only models froze the head).
The Mac never runs the model: for each presenter shot it cuts the reel's own voice for that window, puts it with the
character's picture into the Kaggle input dataset, pushes the kernel, waits, and downloads the clip. Two kernels (A and B)
render two shots at the same time. Kaggle's GPU time is about 30 hours a week, so a weekly budget is kept here and a reel
falls back to the classic style when the budget, the SSD or the character is missing.

Character pack: ~/ReelStudio/library/presenter/<name>/  (REELSTUDIO_PRESENTER_DIR)
    pack.json  {"name", "ai_generated": true, "prompt": "how the character looks and speaks",
                "shots": {"studio": ["studio_front.png", ...], "fast": ["outdoor_walk.png", ...]}}
    plus the images. An older pack with only anchor.png + prompt.txt still works.
Kaggle folders: REELSTUDIO_KAGGLE_DIR (default /Volumes/AI-Models/kaggle) with it/{ds,k} and itb/{ds,k}: the kernel code
and the patch file made in testing stay there; this module only replaces job.json, the audio and the picture.
"""
import json, math, os, shutil, subprocess, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import config

KDIR = Path(config.get("REELSTUDIO_KAGGLE_DIR", "/Volumes/AI-Models/kaggle"))
OWNER = config.get("REELSTUDIO_KAGGLE_OWNER", "weoneaviation")
KAGGLE = config.get("REELSTUDIO_KAGGLE_BIN", "kaggle")
PACK = Path(config.get("REELSTUDIO_PRESENTER_DIR", config.LIBRARY / "presenter" / "pilot_f1"))
LANES = {"A": ("it", "autoreel-talk-input", "autoreel-infinitetalk"), "B": ("itb", "autoreel-talk-input-b", "autoreel-infinitetalk-b")}
WEEKLY_H = float(config.get("REELSTUDIO_KAGGLE_WEEKLY_HOURS", "28"))       # Kaggle shows ~30 h/week; keep a little back
CLIP_H = float(config.get("REELSTUDIO_PRESENTER_CLIP_HOURS", "2.6"))      # measured: one 5-7 s shot ~2.5 GPU-hours
MAX_S = float(config.get("REELSTUDIO_PRESENTER_MAX_SECONDS", "7.5"))      # longest single presenter shot
POLL_S = int(config.get("REELSTUDIO_KAGGLE_POLL_SECONDS", "300"))
TIMEOUT_S = int(config.get("REELSTUDIO_KAGGLE_TIMEOUT_SECONDS", str(5 * 3600)))
SETTLE_S = int(config.get("REELSTUDIO_KAGGLE_SETTLE_SECONDS", "90"))     # a new dataset version needs a moment before the push
FPS_OUT = 60


class Unavailable(RuntimeError):
    """The presenter cannot be made now (no character, no SSD, no budget, Kaggle refused): the reel uses the classic style."""


# ------------------------------------------------------------------ character pack
def pack():
    p = PACK / "pack.json"
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
    elif (PACK / "anchor.png").exists():
        d = {"name": PACK.name, "ai_generated": True, "shots": {"studio": ["anchor.png"], "fast": ["anchor.png"]},
             "prompt": (PACK / "prompt.txt").read_text(encoding="utf-8").strip() if (PACK / "prompt.txt").exists() else ""}
    else:
        return None
    shots = {k: [x for x in v if (PACK / x).exists()] for k, v in (d.get("shots") or {}).items()}
    d["shots"] = {k: v for k, v in shots.items() if v}
    return d if d["shots"] and d.get("ai_generated") is True else None


def image_for(d, style, role, n=0):
    """The character picture for a shot: the style's own pictures first, rotating so hook and CTA differ."""
    lst = d["shots"].get(style) or d["shots"].get("studio") or next(iter(d["shots"].values()))
    k = (0 if role == "hook" else 1) + n
    return PACK / lst[k % len(lst)]


def _warn(log, msg):
    try:
        log(msg, "warn")
    except TypeError:                  # a plain one-argument logger (tests)
        log(msg)


def scene_for(d, scenes, places, group, style, role, n=0, log=lambda m: None):
    """Opt-in per-scene location (spec["presenter_scenes"] = {group id: picture in the pack}, spec["presenter_places"] =
    {picture: "In an aviation classroom ..."}): (picture, place text or None). A shot whose group has no scene, or whose
    picture is missing from the pack, uses the normal rotation (image_for) and the style's own place text."""
    scenes = scenes if isinstance(scenes, dict) else {}; places = places if isinstance(places, dict) else {}
    f = scenes.get(group) if group else None
    if f:
        name = Path(str(f)).name                          # a file in the pack folder, never a path out of it
        if name and (PACK / name).is_file():
            place = places.get(f) or places.get(name)
            return PACK / name, (str(place).strip()[:240] or None) if place else None
        _warn(log, f"AI presenter scene for {group}: {f} is not in the character pack {PACK}; using the normal picture")
    return image_for(d, style, role, n), None


# ------------------------------------------------------------------ weekly GPU budget (Kaggle resets on Sunday 00:00 UTC)
def _week(now=None):
    now = now or datetime.now(timezone.utc)
    sunday = (now - timedelta(days=(now.weekday() + 1) % 7)).date()
    return sunday.isoformat()


def _budget_file():
    return config.APP / "state" / "kaggle_budget.json"


def budget(now=None):
    try:
        b = json.loads(_budget_file().read_text())
    except Exception:  # noqa: BLE001
        b = {}
    if b.get("week") != _week(now):
        b = {"week": _week(now), "used_h": 0.0}
    b["left_h"] = round(WEEKLY_H - b["used_h"], 2)
    return b


def spend(hours, now=None):
    b = budget(now); b["used_h"] = round(b["used_h"] + max(0.0, hours), 3); b.pop("left_h", None)
    f = _budget_file(); f.parent.mkdir(parents=True, exist_ok=True); f.write_text(json.dumps(b))
    return b


def available(n_shots=2):
    """(ok, reason) for making n presenter shots now."""
    if not pack():
        return False, f"no AI character in {PACK} (pack.json with ai_generated true and its pictures)"
    for lane, (d, _, _) in LANES.items():
        if not (KDIR / d / "k" / "kernel.py").exists():
            return False, f"Kaggle kernel {lane} not found in {KDIR / d / 'k'} (is the AI-Models SSD plugged in?)"
    if shutil.which(KAGGLE, path=_env()["PATH"]) is None:
        return False, "the kaggle command is not installed"
    left = budget()["left_h"]
    if left < CLIP_H * n_shots:
        return False, f"Kaggle GPU time left this week: {left:.1f} h (needs about {CLIP_H * n_shots:.1f} h)"
    return True, ""


# ------------------------------------------------------------------ audio
def voice_track(groups: list, end: float, out: Path) -> Path:
    """The reel's voice-over alone, on the reel's own clock (same placement as the mix)."""
    ins, fl = [], []
    for i, g in enumerate(groups):
        s0, e0 = g["trim"]; ins += ["-i", g["file"]]
        fl.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=mono,atrim={s0:.3f}:{e0:.3f},asetpts=PTS-STARTPTS,"
                  f"adelay={int(round(g['t0'] * 1000))}:all=1[a{i}]")
    fl.append("".join(f"[a{i}]" for i in range(len(groups))) + f"amix=inputs={len(groups)}:normalize=0,apad=whole_dur={end:.3f},atrim=0:{end:.3f}[o]")
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", ";".join(fl), "-map", "[o]", "-c:a", "pcm_s16le", str(out)],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"voice track failed: {r.stderr[-300:]}")
    return out


def cut(track: Path, t0: float, dur: float, out: Path) -> Path:
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t0:.3f}", "-i", str(track), "-af", f"apad=whole_dur={dur:.4f}",
                        "-t", f"{dur:.4f}", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(out)], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"audio cut failed: {r.stderr[-300:]}")
    return out


# ------------------------------------------------------------------ Kaggle
def _env():
    return {**os.environ, "PATH": f"/opt/homebrew/bin:/Volumes/AI-Models/tools-env/bin:{os.environ.get('PATH', '')}"}


def _kg(*args, timeout=600):
    r = subprocess.run([KAGGLE, *args], capture_output=True, text=True, timeout=timeout, env=_env())
    out = (r.stdout + r.stderr).strip()
    if r.returncode:
        raise Unavailable(f"kaggle {' '.join(args[:2])}: {out[-240:]}")
    return out


def status(lane):
    s = _kg("kernels", "status", f"{OWNER}/{LANES[lane][2]}", timeout=120).upper()
    for k in ("COMPLETE", "ERROR", "CANCEL", "RUNNING", "QUEUED"):
        if k in s:
            return k
    return "UNKNOWN"


def prompt_for(d, style, role, place=None):
    mood = {"hook": "Speaks with confident energy, eyebrows and hands supporting the first words, natural blinking.",
            "cta": "Ends warmly with a small nod and an inviting look into the camera.",
            "proof": "Explains calmly and clearly, steady eye contact, small natural gestures."}.get(role, "")
    place = place or (d.get("places") or {}).get(style) or {"studio": "Seated in a bright minimal studio, camera steady at chest height.",
                                                    "fast": "Standing outdoors, slight handheld camera feel, natural daylight."}.get(style, "")
    return " ".join(x for x in (d.get("prompt", ""), place, mood, "Lips move exactly with the speech; silent when there is no speech.") if x)


def submit(lane, item, audio: Path, image: Path, log):
    """Put one shot into lane's dataset and start its kernel. item = {"name", "prompt"}."""
    d, ds_slug, _ = LANES[lane]; ds = KDIR / d / "ds"
    for f in ds.glob("*.wav"):
        f.unlink()
    for f in ds.glob("shot_*.png"):
        f.unlink()
    img = f"shot_{item['name']}{image.suffix.lower()}"
    shutil.copy(audio, ds / f"{item['name']}.wav"); shutil.copy(image, ds / img)
    job = {"prompt": item["prompt"], "items": [{"name": item["name"], "image": img, "audio": f"{item['name']}.wav", "prompt": item["prompt"]}],
           "steps": int(config.get("REELSTUDIO_PRESENTER_STEPS", "8")), "teacache": False,
           "quant_file": "infinitetalk_single_int8_lora", "size": "infinitetalk-480"}
    (ds / "job.json").write_text(json.dumps(job, indent=1), encoding="utf-8")
    _kg("datasets", "version", "-p", str(ds), "-m", f"reel {item['name']} {int(time.time())}", "--dir-mode", "skip")
    time.sleep(SETTLE_S)
    _kg("kernels", "push", "-p", str(KDIR / d / "k"))
    log(f"AI presenter {item['name']}: sent to Kaggle kernel {lane}")
    return time.time()


def collect(lane, name, out_dir: Path) -> Path:
    shutil.rmtree(out_dir, ignore_errors=True); out_dir.mkdir(parents=True)
    _kg("kernels", "output", f"{OWNER}/{LANES[lane][2]}", "-p", str(out_dir), timeout=1800)
    f = out_dir / f"{name}.mp4"
    if not f.exists():
        raise Unavailable(f"Kaggle finished but {name}.mp4 is missing ({', '.join(p.name for p in out_dir.iterdir())[:160]})")
    return f


def prep(src: Path, out: Path, dur: float) -> Path:
    """Presenter clip -> the reel's 1296x2304 60 fps working canvas (scaled to cover, centred); holds the last frame if short."""
    vf = (f"fps={FPS_OUT},scale=1296:2304:force_original_aspect_ratio=increase:flags=lanczos,crop=1296:2304,"   # covers any aspect
          f"tpad=stop_mode=clone:stop_duration={dur:.2f},eq=contrast=1.03:saturation=1.0,format=yuv420p")
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", vf, "-t", f"{dur:.2f}", "-an",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", str(out)], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"presenter prep failed: {r.stderr[-300:]}")
    return out


def make(shots: list, style: str, work: Path, log, trace, check_cancel, scenes=None, places=None) -> dict:
    """shots = [{"id", "role", "t0", "need", "track", "group"?}] -> {id: {"file" (1296x2304 60 fps), "raw", "gpu_h", "lane"}}.
    Up to two shots render at the same time (kernels A and B); the rest wait for a free kernel.
    scenes/places: the spec's optional presenter_scenes / presenter_places (see scene_for)."""
    d = pack()
    if not d:
        raise Unavailable("no AI character")
    work.mkdir(parents=True, exist_ok=True)
    state_p = work / "kaggle.json"
    st = json.loads(state_p.read_text()) if state_p.exists() else {}        # survives a Mac restart mid-render
    # a shot that failed before is not tried again here (it would cost hours): the reel shows clips there instead
    todo = [s for s in shots if s["id"] not in st or st[s["id"]].get("state") not in ("done", "failed")]
    t_start = time.time(); why = ""
    while todo:
        check_cancel()
        busy = {v["lane"] for v in st.values() if v.get("state") == "running"}
        for s in todo:
            if s["id"] in st and st[s["id"]].get("state") == "running":
                continue
            free = [l for l in LANES if l not in busy]
            if not free:
                break
            lane = free[0]; dur = min(s["need"], MAX_S) + 0.25
            aud = cut(s["track"], s["t0"], dur, work / f"{s['id']}.wav")
            img, place = scene_for(d, scenes, places, s.get("group"), style, s["role"], len(st), log)
            trace(s["id"], "run", f"Kaggle {lane}: {dur:.1f}s talking clip")
            t0 = submit(lane, {"name": s["id"], "prompt": prompt_for(d, style, s["role"], place)}, aud, img, log)
            st[s["id"]] = {"lane": lane, "state": "running", "t0": t0, "need": s["need"]}; busy.add(lane)
            state_p.write_text(json.dumps(st))
        for sid, v in list(st.items()):
            if v.get("state") != "running":
                continue
            k = status(v["lane"])
            if k == "COMPLETE":
                hours = (time.time() - v["t0"]) / 3600; spend(hours)
                raw = collect(v["lane"], sid, work / f"out_{sid}")
                out = prep(raw, work / f"{sid}_9x16.mp4", v["need"])
                st[sid] = {**v, "state": "done", "raw": str(raw), "file": str(out), "gpu_h": round(hours, 2)}
                log(f"AI presenter {sid}: ready after {hours:.1f} h on Kaggle {v['lane']}")
                trace(sid, "done", f"{hours:.1f} h on Kaggle")
            elif k in ("ERROR", "CANCEL"):
                spend((time.time() - v["t0"]) / 3600)
                st[sid] = {**v, "state": "failed"}; state_p.write_text(json.dumps(st))
                why = f"Kaggle kernel {v['lane']} stopped with {k} on {sid}"; log(f"AI presenter {sid}: {why}"); trace(sid, "fail", why[:80])
            elif time.time() - v["t0"] > TIMEOUT_S:
                spend(TIMEOUT_S / 3600)
                st[sid] = {**v, "state": "failed"}; state_p.write_text(json.dumps(st))
                why = f"Kaggle took longer than {TIMEOUT_S // 3600} h on {sid}"; log(f"AI presenter {sid}: {why}"); trace(sid, "fail", why[:80])
            state_p.write_text(json.dumps(st))
        todo = [s for s in shots if st.get(s["id"], {}).get("state") not in ("done", "failed")]
        if todo:
            for _ in range(max(1, POLL_S // 10)):
                check_cancel(); time.sleep(min(10, POLL_S))
    done = {sid: v for sid, v in st.items() if v.get("state") == "done" and sid in {x["id"] for x in shots}}
    if not done:                         # nothing usable: the reel uses the classic style
        raise Unavailable(why or "no presenter shot could be made")
    log(f"AI presenter: {len(done)} of {len(shots)} shot(s) in {(time.time() - t_start) / 3600:.1f} h")
    return done

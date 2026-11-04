"""AI Video: lip-synced presenter shots made on this Mac by Phosphene (LTX-2.5 audio-to-video, Apple MLX).

The presenter says exactly the reel's own voice: each shot gets the voice-over cut for its time window, Phosphene draws the
face speaking to that audio (portrait 448x768, 24 fps), and the clip is scaled to the reel's 9:16 canvas.
Phosphene and the voice model never run together on a 16 GB Mac: the panel is started here and stopped again afterwards.
"""
import json, math, os, re, shutil, signal, subprocess, time, urllib.request
from pathlib import Path

from .. import config

PH_DIR = Path(config.get("REELSTUDIO_PHOSPHENE_DIR", "/Volumes/AI-Models/phosphene"))
PORT = int(config.get("REELSTUDIO_PHOSPHENE_PORT", "8198"))
BASE = f"http://127.0.0.1:{PORT}"
PACK = Path(config.get("REELSTUDIO_PRESENTER_DIR", config.LIBRARY / "presenter" / "pilot_f1"))
TOOLS_PY = config.get("REELSTUDIO_TOOLS_PY", "/Volumes/AI-Models/tools-env/bin/python")
W, H, FPS = 448, 768, 24
MAX_FRAMES = 241          # 10.04 s, the longest single take this tier renders well
SYNC_OK = 0.20            # lipsync_check.py: >= 0.25 sync, < 0.20 voice-over; in between is kept but flagged


def frames_for(sec: float) -> int:
    n = max(25, math.ceil(sec * FPS) + 1)
    while n % 8 != 1:
        n += 1
    return min(n, MAX_FRAMES)


def available():
    """(ok, reason). Everything AI Video needs on this Mac."""
    if not (PH_DIR / "mlx_ltx_panel.py").exists():
        return False, f"Phosphene not found at {PH_DIR} (is the AI-Models SSD plugged in?)"
    if not any((PH_DIR / "mlx_models").glob("ltx-2.5-mlx-q4*")):
        return False, "Phosphene's LTX-2.5 model pack is missing"
    if not (PACK / "anchor.png").exists():
        return False, f"presenter pack missing: {PACK / 'anchor.png'}"
    return True, ""


def _http(path, timeout=15):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:          # loopback only
        return json.loads(r.read().decode())


def _curl(args, timeout=60):
    r = subprocess.run(["curl", "-s", "-m", str(timeout), *args], capture_output=True, text=True, timeout=timeout + 5)
    try:
        return json.loads(r.stdout)
    except Exception:  # noqa: BLE001
        raise RuntimeError(f"Phosphene answered: {r.stdout[:200]} {r.stderr[:200]}")


def _up():
    try:
        return _http("/status", timeout=5)
    except Exception:  # noqa: BLE001
        return None


class Panel:
    """Starts the Phosphene panel if it is not running; on close frees its memory (and stops it if we started it)."""

    def __init__(self, log):
        self.log = log; self.proc = None

    def __enter__(self):
        if _up() is None:
            py = PH_DIR / "ltx-2-mlx" / "env" / "bin" / "python3.11"
            env = {**os.environ, "PATH": "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", ""), "LTX_PORT": str(PORT)}
            lf = open(PH_DIR.parent / "_panel_reelstudio.log", "a")
            self.proc = subprocess.Popen([str(py), "mlx_ltx_panel.py"], cwd=PH_DIR, env=env, stdout=lf, stderr=subprocess.STDOUT,
                                         start_new_session=True)
            for _ in range(90):
                time.sleep(2)
                if _up() is not None:
                    break
                if self.proc.poll() is not None:
                    raise RuntimeError("Phosphene panel exited during start-up")
            else:
                raise RuntimeError("Phosphene panel did not start within 3 minutes")
            self.log("AI presenter engine started")
        return self

    def __exit__(self, *a):
        try:
            st = _up() or {}
            pid = (st.get("helper") or {}).get("pid")
            if pid and not st.get("running"):
                os.kill(int(pid), signal.SIGTERM)          # the model process holds ~8 GB; give it back to the voice model
        except Exception:  # noqa: BLE001
            pass
        if self.proc and self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(20)
            except Exception:  # noqa: BLE001
                os.killpg(self.proc.pid, signal.SIGKILL)
        return False


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


def cut(track: Path, t0: float, frames: int, out: Path) -> Path:
    dur = frames / FPS
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t0:.3f}", "-i", str(track), "-af", f"apad=whole_dur={dur:.4f}",
                        "-t", f"{dur:.4f}", "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(out)], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"audio cut failed: {r.stderr[-300:]}")
    return out


def sync_score(mp4: Path):
    script = PH_DIR / "scripts" / "lipsync_check.py"
    if not (script.exists() and Path(TOOLS_PY).exists()):
        return None
    r = subprocess.run([TOOLS_PY, str(script), str(mp4)], capture_output=True, text=True, timeout=300, cwd=PH_DIR)
    m = re.search(r"best r=([+-]?\d+\.\d+)", r.stdout)
    return float(m.group(1)) if m else None


def _render(audio: Path, frames: int, prompt: str, seed: int, out: Path, log, check_cancel, timeout=2400):
    up = _curl(["-F", f"image=@{PACK / 'anchor.png'}", BASE + "/upload"])["path"]
    staged = PH_DIR / "panel_uploads" / f"rs_{out.stem}_{seed}.wav"; shutil.copy(audio, staged)
    q = _curl(["-X", "POST", BASE + "/queue/add", "--data-urlencode", "mode=a2v", "--data-urlencode", f"image={up}",
               "--data-urlencode", f"audio={staged}", "--data-urlencode", f"prompt={prompt}",
               "-d", f"width={W}", "-d", f"height={H}", "-d", f"frames={frames}", "-d", f"frame_rate={FPS}",
               "-d", f"seed={seed}", "-d", "quality=quick", "-d", "enhance=false"])
    jid = q.get("id")
    if not jid:
        raise RuntimeError(f"Phosphene refused the shot: {q}")
    t0 = time.time()
    while time.time() - t0 < timeout:
        check_cancel(); time.sleep(10)
        st = _up() or {}
        h = next((x for x in st.get("history", []) if x.get("id") == jid), None)
        if h and h.get("status") == "done" and h.get("output_path"):
            shutil.copy(h["output_path"], out)
            return {"file": str(out), "sec": round(time.time() - t0), "seed": seed}
        if h and h.get("status") in ("failed", "error", "cancelled"):
            raise RuntimeError(f"Phosphene shot failed: {str(h.get('error') or h.get('status'))[:200]}")
    try:
        _curl(["-X", "POST", BASE + "/stop"])
    except Exception:  # noqa: BLE001
        pass
    raise RuntimeError("Phosphene shot took longer than 40 minutes")


def prep(src: Path, out: Path, dur: float) -> Path:
    """448x768 presenter -> the reel's 1296x2304 60 fps working canvas; holds the last frame if the shot runs longer."""
    vf = ("fps=60,scale=1296:-2:flags=lanczos,crop=1296:2304:0:'(ih-oh)/2',"
          f"tpad=stop_mode=clone:stop_duration={dur:.2f},eq=contrast=1.04:saturation=1.0,format=yuv420p")
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", vf, "-t", f"{dur:.2f}", "-an",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", str(out)], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"presenter prep failed: {r.stderr[-300:]}")
    return out


def prompt_for(role: str) -> str:
    base = (PACK / "prompt.txt").read_text(encoding="utf-8").strip()
    mood = {"hook": "She grabs attention with an energetic, confident greeting and lively expressions.",
            "cta": "She ends with a warm, inviting smile and a small nod."}.get(role, "")
    return f"{base} {mood}".strip()


def make(shots: list, work: Path, log, trace, check_cancel) -> dict:
    """shots = [{"id", "role", "t0", "need", "track"}] -> {id: {"file" (1296x2304 60 fps), "raw", "sync", "seed", "sec"}}"""
    work.mkdir(parents=True, exist_ok=True); done = {}
    with Panel(log):
        for s in shots:
            frames = frames_for(s["need"]); aud = cut(s["track"], s["t0"], frames, work / f"{s['id']}_voice.wav")
            best = None
            for seed in (101, 211):                      # one retry when the mouth does not follow the voice
                trace(s["id"], "run", f"rendering {frames / FPS:.1f}s (seed {seed})")
                r = _render(aud, frames, prompt_for(s["role"]), seed, work / f"{s['id']}_s{seed}.mp4", log, check_cancel)
                r["sync"] = sync_score(Path(r["file"]))
                log(f"AI presenter {s['id']}: {r['sec']}s, lip-sync r={r['sync']}")
                if best is None or (r["sync"] or -9) > (best["sync"] or -9):
                    best = r
                if r["sync"] is None or r["sync"] >= SYNC_OK:
                    break
            out = prep(Path(best["file"]), work / f"{s['id']}.mp4", s["need"])
            done[s["id"]] = {**best, "raw": best["file"], "file": str(out)}
            trace(s["id"], "done", f"lip-sync {best['sync']}")
    return done

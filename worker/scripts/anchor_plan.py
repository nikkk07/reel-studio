"""Plan an anchor-mode reel from a job whose voice is recorded and checked (make_local --until voice).

    python worker/scripts/anchor_plan.py ~/ReelStudio/jobs/<id> --cut G2:1 --cut G3:0,G3:1 [--clip heygen.mp4] [--out spec_anchor.json]

--cut GROUP:PHRASE marks a phrase as a full-screen cutaway (stock footage); every other phrase is said by the presenter on
camera. It writes:
  <job>/anchor/oncam.wav         the on-camera parts of the voice-over joined in order (0.1 s padding, 20 ms crossfades):
                                 upload this one file to HeyGen (scripts/heygen_avatar.py make --audio oncam.wav)
  <job>/anchor/spec_anchor.json  a copy of spec.json with spec["anchor"] = {"clip", "map"} (each on-camera window of the
                                 reel -> where it starts in the HeyGen clip) and the shots of the cut phrases marked cutaway
With --apply the job's own spec.json becomes the anchor spec (old one kept as spec.before_anchor.json), so
`make_local.py <spec> --job <id>` resumes it in anchor mode once the HeyGen clip is at --clip.
It prints the windows, plus warnings (a cutaway window over 4 s, a cutaway window with no shot anchored in it).
"""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, soundfile as sf  # noqa: E402
from reelworker.media import timeline  # noqa: E402

PAD, XFADE, SR = 0.1, 0.02, 48000


def vo_map(job: Path) -> dict:
    out = json.loads((job / "voice_out.json").read_text(encoding="utf-8"))
    return {gid: {"file": v["file"], "trim": v["trim"], "spans": v["spans"]} for gid, v in out.items()}


def parse_cuts(items):
    cuts = set()
    for it in items or []:
        for x in str(it).split(","):
            if x.strip():
                g, _, k = x.strip().partition(":")
                cuts.add((g.strip(), int(k or 0)))
    return cuts


def plan(spec, vo, cuts, pad=PAD):
    """-> (TL, on-camera windows [(t0, t1, [(group, phrase)...])], cutaway windows [(t0, t1, [(group, phrase)...])])."""
    TL = timeline.build({k: v for k, v in spec.items() if k not in ("anchor", "scenes", "captions")}, vo, "", "")
    ph = [(g["id"], k, a, b) for g in TL["groups"] for k, (a, b) in enumerate(g["spans"])]
    bad = cuts - {(g, k) for g, k, _, _ in ph}
    if bad:
        raise SystemExit(f"unknown phrases in --cut: {sorted(bad)} (groups/phrases: {[(g, k) for g, k, _, _ in ph]})")
    runs = []
    for g, k, a, b in ph:
        cam = (g, k) not in cuts
        if runs and runs[-1][0] == cam:
            runs[-1][1].append((g, k, a, b))
        else:
            runs.append([cam, [(g, k, a, b)]])
    on, off, end = [], [], TL["vo_end"]
    for i, (cam, items) in enumerate(runs):
        a, b = items[0][2], items[-1][3]
        prev_end = runs[i - 1][1][-1][3] if i else None
        nxt = runs[i + 1][1][0][2] if i + 1 < len(runs) else None
        if cam:
            pre = min(pad, a) if prev_end is None else min(pad, (a - prev_end) / 2)
            post = min(pad, end + 0.4 - b) if nxt is None else min(pad, (nxt - b) / 2)
            on.append((round(a - pre, 3), round(b + post, 3), [(g, k) for g, k, _, _ in items]))
        else:
            off.append((a, b, [(g, k) for g, k, _, _ in items]))
    return TL, on, off


def track(TL, end):
    """The reel's voice-over on the reel's own clock (same placement as the mix), mono 48 kHz."""
    import librosa
    n = int(end * SR) + 1; out = np.zeros(n)
    for g in TL["groups"]:
        a, sr = sf.read(g["file"]); a = a if a.ndim == 1 else a.mean(1)
        a = a[int(g["trim"][0] * sr):int(g["trim"][1] * sr)]
        if sr != SR:
            a = librosa.resample(np.asarray(a, np.float64), orig_sr=sr, target_sr=SR)
        s = int(round(g["t0"] * SR)); e = min(n, s + len(a)); out[s:e] += a[:e - s]
    return out


def join(vo_track, windows, xfade=XFADE):
    """Cut the windows out of the voice track and join them with short crossfades -> (audio, start of each window in it)."""
    n = int(round(xfade * SR)); out = np.zeros(0); starts = []
    for k, (a, b, _) in enumerate(windows):
        seg = vo_track[int(round(a * SR)):int(round(b * SR))].copy()
        if k == 0 or n == 0 or len(out) < n or len(seg) < n:
            starts.append(len(out) / SR); out = np.concatenate([out, seg]); continue
        starts.append((len(out) - n) / SR)
        ramp = np.linspace(0, 1, n)
        out[-n:] = out[-n:] * np.sqrt(1 - ramp) + seg[:n] * np.sqrt(ramp)       # equal-power crossfade
        out = np.concatenate([out, seg[n:]])
    return out, starts


def make(job: Path, cuts, spec_path=None, clip=None, out=None, pad=PAD, apply=False):
    spec = json.loads(Path(spec_path or job / "spec.json").read_text(encoding="utf-8"))
    TL, on, off = plan(spec, vo_map(job), cuts, pad)
    if not on:
        raise SystemExit("every phrase is a cutaway: nothing for the presenter to say")
    adir = job / "anchor"; adir.mkdir(parents=True, exist_ok=True)
    audio, starts = join(track(TL, TL["vo_end"] + 0.5), on)
    sf.write(adir / "oncam.wav", audio.astype(np.float32), SR, subtype="PCM_16")
    amap = [{"vo_t0": a, "vo_t1": b, "clip_t0": round(s, 4)} for (a, b, _), s in zip(on, starts)]
    cutset = {x for _, _, ps in off for x in ps}
    marked = []
    for s in spec.get("shots", []):
        if (s.get("at_group"), int(s.get("at_phrase") or 0)) in cutset:
            s["cutaway"] = True; marked.append(s["id"])
    spec["anchor"] = {**(spec.get("anchor") or {}), "clip": str(clip or adir / "heygen.mp4"), "map": amap}   # keeps "punch"
    dst = Path(out or adir / "spec_anchor.json"); dst.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    if apply:                                  # the resumed job (make_local --job <id>) then renders in anchor mode
        sp = job / "spec.json"
        if sp.exists() and not (job / "spec.before_anchor.json").exists():
            (job / "spec.before_anchor.json").write_text(sp.read_text(encoding="utf-8"), encoding="utf-8")
        sp.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    warn = []
    for a, b, ps in off:
        shots = [s["id"] for s in spec.get("shots", []) if s.get("cutaway") and (s.get("at_group"), int(s.get("at_phrase") or 0)) in set(ps)]
        if b - a > 4.0:
            warn.append(f"cutaway {ps} lasts {b - a:.1f} s (keep it within ~4 s; it is cut into ~2 s clips)")
        if not shots:
            warn.append(f"cutaway {ps} has no shot anchored in it (another cutaway clip will be borrowed)")
    res = {"oncam": str(adir / "oncam.wav"), "oncam_seconds": round(len(audio) / SR, 3), "spec": str(dst), "map": amap,
           "cutaways": [{"phrases": ps, "t0": round(a, 3), "t1": round(b, 3)} for a, b, ps in off], "marked_cutaway": marked, "warnings": warn}
    return res


def main():
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("job"); a.add_argument("--cut", action="append", default=[], help="GROUP:PHRASE[,GROUP:PHRASE] said off camera")
    a.add_argument("--spec"); a.add_argument("--clip", help="where the HeyGen clip will be"); a.add_argument("--out")
    a.add_argument("--pad", type=float, default=PAD)
    a.add_argument("--apply", action="store_true", help="also make it the job's spec.json (the old one is kept as spec.before_anchor.json)")
    o = a.parse_args()
    print(json.dumps(make(Path(o.job).expanduser(), parse_cuts(o.cut), o.spec, o.clip, o.out, o.pad, o.apply), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

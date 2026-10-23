"""Audio: voice placement, room tone, synthesized SFX keyed to cuts/graphics, mood music, ducking."""
import numpy as np, soundfile as sf
from . import sfxlib as sfx

BPM = {"tense": 108, "dramatic": 100, "curious": 96, "calm": 84, "uplifting": 100}
DRONE = {"tense": 49, "dramatic": 46, "curious": 52, "calm": 55, "uplifting": 55}


def thunder(SR, d=2.6, level=0.8, seed=0):
    rng = np.random.default_rng(seed); n = int(d * SR); x = np.cumsum(rng.normal(0, 1, n)); x -= sfx.lp_fast(x, 2)
    x = sfx.lp_fast(x, 180); x /= np.abs(x).max() + 1e-9
    tt = np.arange(n) / SR; env = (1 - np.exp(-tt / 0.01)) * np.exp(-tt / (d * 0.35))
    crack = sfx.hp_fast(rng.normal(0, 1, n) * np.exp(-tt / 0.05), 1500) * 0.6
    return level * (x * env + crack)


def build(TL: dict, roomtone_path: str, out_path: str):
    import librosa
    from scipy.signal import butter, sosfilt
    SR = sfx.SR; END = TL["end"]; N = int(END * SR) + SR
    vo, fx, mus = np.zeros(N), np.zeros(N), np.zeros(N)

    def put(buf, x, t, g=1.0):
        s = int(max(0, t) * SR); e = min(N, s + len(x))
        if e > s: buf[s:e] += g * x[:e - s]

    for g in TL["groups"]:
        a, sr = sf.read(g["file"]); a = a if a.ndim == 1 else a.mean(1)
        s0, e0 = g["trim"]; a = a[int(s0 * sr):int(e0 * sr)]
        x = librosa.resample(np.asarray(a, dtype=np.float64), orig_sr=sr, target_sr=SR)
        loud = np.abs(x) > 0.01
        r = np.sqrt(np.mean(x[loud] ** 2)) if loud.any() else 0.1
        x = x * (0.12 / (r + 1e-9)); f = int(0.015 * SR); x[:f] *= np.linspace(0, 1, f); x[-f:] *= np.linspace(1, 0, f)
        put(vo, x, g["t0"])
    vo_end = TL["vo_end"]
    try:
        rt, rsr = sf.read(roomtone_path); rt = rt if rt.ndim == 1 else rt.mean(1)
        rt = librosa.resample(rt, orig_sr=rsr, target_sr=SR)
        bed = np.tile(rt, int(N / len(rt)) + 2)[:N]; bed = bed / (np.sqrt(np.mean(bed ** 2)) + 1e-9) * 0.0035
        fade = np.ones(N); e = int((vo_end + 0.2) * SR); fade[e:] = 0; fade[max(0, e - int(0.3 * SR)):e] = np.linspace(1, 0, min(e, int(0.3 * SR)))
        vo = vo + bed * fade
    except Exception:  # noqa: BLE001  (room tone is optional)
        pass
    vo = sosfilt(butter(2, 75 / (SR / 2), 'high', output='sos'), vo)
    env = sfx.lp_fast(np.abs(vo), 12); thr = 0.10
    vo = vo * np.where(env > thr, (thr + (env - thr) / 2.2) / (env + 1e-9), 1.0)

    # SFX keyed to edits and graphics
    for s in TL["shots"][1:]:
        put(fx, sfx.whoosh(0.45, level=0.26), s["t0"] - 0.22)
    seed = 1
    for g in TL["graphics"]:
        t0, ty = g["t0"], g["type"]
        if ty == "flash":
            put(fx, thunder(SR, 2.6, 0.9, seed), t0 - 0.04); put(fx, sfx.impact(1.4, 0.8), t0 - 0.02, 0.7); seed += 1
        elif ty in ("big_text", "quote_card", "counter"):
            put(fx, sfx.impact(1.1, 0.6), t0 + 0.05, 0.6)
            if ty == "counter":
                for j in range(int(0.9 / 0.04)): put(fx, sfx.tick(f=2200), t0 + 0.1 + j * 0.04, 0.28)
        elif ty in ("chip", "compare"):
            put(fx, sfx.ui_pop(), t0 + 0.05)
        elif ty == "list_card":
            for r in g.get("reveal", [t0]): put(fx, sfx.ui_pop(), r - 0.1)
        elif ty == "timer":
            tt = t0
            while tt < g["t1"]: put(fx, sfx.tick(f=1800, level=0.12), tt); tt += 1.0
        elif ty == "shake":
            put(fx, sfx.impact(1.3, 0.9), t0, 0.8)
        elif ty == "dim":
            put(fx, sfx.drone(1.4, 60, 0.35), t0)
        elif ty == "plane_path":
            put(fx, sfx.riser(1.6, 0.22), t0 + 0.3)
    last = TL["groups"][-1]["t0"]
    put(fx, sfx.riser(1.0, 0.2), last - 1.0); put(fx, sfx.impact(1.6, 0.6), vo_end - 0.1, 0.7)

    mood = TL.get("mood", "curious")
    first2 = TL["groups"][1]["t0"] if len(TL["groups"]) > 1 else 1.5
    put(mus, sfx.drone(END, DRONE.get(mood, 52), 0.13), 0)
    put(mus, sfx.pulse_bed(max(1.0, last - first2 + 0.2), BPM.get(mood, 96), 0.38), first2 - 0.1)
    put(mus, sfx.pad(END - last + 0.3, freqs=(220, 277.18, 329.63, 440), level=0.15), last - 0.2)
    duck = 1 - 0.6 * np.clip(sfx.lp_fast(np.abs(vo), 6) / 0.05, 0, 1)
    mix = vo + 0.7 * fx + 0.85 * mus * duck
    mix = mix[:int(END * SR)]; o = int(0.5 * SR); mix[-o:] *= np.linspace(1, 0, o)
    sf.write(out_path, np.stack([mix, mix], 1).astype(np.float32), SR, subtype="FLOAT")
    return out_path

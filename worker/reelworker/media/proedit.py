"""Pro edit layer for ad reels, all opt-in through spec.json (a spec without these fields renders exactly as before).

- Background swap: the AI presenter stays the anchor while the place behind her changes. Her person mask (matte.py) cuts her
  out; a stock B-roll plate goes behind her with a slow push-in and sideways drift, a touch of depth-of-field blur, a feathered
  edge, a 1-2 px light wrap and a gentle LAB colour match so she sits in the new light.     swap_shot(...)
- Colour grade: one consistent look for the whole video (gentle S-curve, warm highlights / teal shadows, a little more
  colour), done with per-channel 8-bit LUTs so it costs about a millisecond a frame.        apply_grade(frame, "warm_cinematic")
Sizes are tuned on the 1296x2304 working canvas and scale with the frame width.
"""
import subprocess
from pathlib import Path
import cv2, numpy as np

BASE_W = 1296                  # the working canvas every shot is prepared at (footage.prep_shot, presenter.prep)
GRADES = ("warm_cinematic", "clean")


def _px(n, w):
    """n pixels on the 1296-wide canvas, scaled to a frame w pixels wide."""
    return max(0.5, n * w / BASE_W)


# ------------------------------------------------------------------ background swap
def feather(mask, erode=3.0, soft=2.5):
    """Person mask (uint8 0-255 or float 0-1) -> float32 alpha 0-1: pulled in ~3 px (no halo of the old room around her)
    and softened ~2.5 px so the edge never looks cut out with scissors."""
    m = mask.astype(np.float32) / 255.0 if mask.dtype == np.uint8 else mask.astype(np.float32)
    w = m.shape[1]; e = int(round(_px(erode, w)))
    if e > 0:
        m = cv2.erode(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * e + 1, 2 * e + 1)))
    m = cv2.GaussianBlur(m, (0, 0), _px(soft, w))
    return np.clip(m, 0.0, 1.0)


def plate(bg, k, size, z1=1.06, dof=2.0):
    """The background at shot progress k (0-1): slow push-in 1.00 -> z1 and a sideways drift that only uses the room the
    push-in makes (never shows an edge), then a slight blur so it reads as further away than the presenter."""
    h, w = size; bh, bw = bg.shape[:2]; k = float(np.clip(k, 0.0, 1.0))
    z = 1.0 + (z1 - 1.0) * k
    s = max(w / bw, h / bh) * z
    room = (bw * s - w) / 2                              # horizontal slack on each side at this zoom
    dx = 0.6 * room * (2 * k - 1) if k > 0 else 0.0       # left -> right, inside the slack
    dx = float(np.clip(dx, -room, room))
    M = np.float32([[s, 0, w / 2 - s * bw / 2 + dx], [0, s, h / 2 - s * bh / 2]])
    out = cv2.warpAffine(bg, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    return cv2.GaussianBlur(out, (0, 0), _px(dof, w)) if dof > 0 else out


def match_params(people, masks, plates):
    """LAB mean/std of the presenter (inside her mask) and of the background plate, over a few frames (one fixed transform
    for the whole shot: per-frame stats would make her colour flicker)."""
    fg, bgp = [], []
    for fr, m in zip(people, masks):
        lab = cv2.cvtColor(fr, cv2.COLOR_BGR2LAB).reshape(-1, 3)
        sel = (m.reshape(-1) > (128 if m.dtype == np.uint8 else 0.5))
        if sel.any():
            fg.append(lab[sel][::7])
    for b in plates:
        bgp.append(cv2.cvtColor(b, cv2.COLOR_BGR2LAB)[::4, ::4].reshape(-1, 3))
    if not fg or not bgp:
        return None
    fg = np.concatenate(fg).astype(np.float32); bgp = np.concatenate(bgp).astype(np.float32)
    return {"mu_p": fg.mean(0), "sd_p": fg.std(0) + 1e-3, "mu_b": bgp.mean(0), "sd_b": bgp.std(0) + 1e-3}


def color_match(bgr, P, strength=0.3):
    """Gentle Reinhard transfer of the presenter toward the plate's light (strength 0 = unchanged, 1 = full transfer)."""
    if not P or strength <= 0:
        return bgr
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    scale = np.clip(1 + strength * (P["sd_b"] / P["sd_p"] - 1), 0.85, 1.2)
    lab = (lab - P["mu_p"]) * scale + P["mu_p"] + strength * (P["mu_b"] - P["mu_p"])
    return cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def composite(person, mask, bg, k, P=None, strength=0.3, wrap=0.65):
    """One frame: presenter * alpha + plate * (1 - alpha), with a light wrap (the blurred plate bleeding ~1.5 px into her edge)."""
    h, w = person.shape[:2]
    if mask.shape[:2] != (h, w):
        mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_LINEAR)
    a = feather(mask)
    pl = plate(bg, k, (h, w)).astype(np.float32)
    fg = color_match(person, P, strength).astype(np.float32)
    out = fg * a[..., None] + pl * (1 - a[..., None])
    glow = cv2.GaussianBlur(pl, (0, 0), _px(5, w))
    lw = np.clip(cv2.GaussianBlur(1 - a, (0, 0), _px(1.5, w)) * a, 0, 1) * wrap
    out += lw[..., None] * (glow - out)
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def _frames(path):
    cap = cv2.VideoCapture(str(path)); n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)); fps = cap.get(cv2.CAP_PROP_FPS) or 60
    return cap, n, fps


def _sample(path, n_total, idxs):
    cap = cv2.VideoCapture(str(path)); out = []
    for i in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, min(i, max(0, n_total - 1))); ok, fr = cap.read()
        if ok:
            out.append(fr)
    cap.release()
    return out


def swap_shot(person: Path, matte: Path, bg: Path, out: Path, strength=0.3) -> Path:
    """Presenter clip + its person-mask clip + a prepared B-roll clip -> one clip with her in the new place (same size, fps
    and frame count as the presenter clip). A plate shorter than the shot holds its last frame."""
    if out.exists() and out.stat().st_size > 0 and all(out.stat().st_mtime >= Path(p).stat().st_mtime for p in (person, matte, bg)):
        return out
    cp, n, fps = _frames(person); cm, nm, _ = _frames(matte); cb, nb, _ = _frames(bg)
    w, h = int(cp.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cp.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if n <= 0 or nm <= 0 or nb <= 0:
        raise RuntimeError("background swap: an input clip has no frames")
    idx = sorted({int(n * f) for f in (0.05, 0.3, 0.55, 0.8)})
    pf = _sample(person, n, idx); mf = [x[..., 0] for x in _sample(matte, nm, idx)]
    bf = [plate(x, i / max(1, n - 1), (h, w), dof=0) for x, i in zip(_sample(bg, nb, idx), idx)]
    P = match_params(pf, [cv2.resize(m, (w, h)) for m in mf], bf)
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", f"{fps}", "-i", "-",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p", str(out)], stdin=subprocess.PIPE)
    last_m = last_b = None
    try:
        for i in range(n):
            ok, fr = cp.read()
            if not ok:
                break
            okm, m = cm.read(); okb, b = cb.read()
            m = m[..., 0] if okm else last_m; b = b if okb else last_b
            if m is None or b is None:
                raise RuntimeError("background swap: mask or plate ended early")
            last_m, last_b = m, b
            enc.stdin.write(composite(fr, m, b, i / max(1, n - 1), P, strength).tobytes())
    except Exception:
        enc.stdin.close(); enc.wait(); out.unlink(missing_ok=True)
        raise
    enc.stdin.close(); enc.wait()
    if enc.returncode != 0:
        out.unlink(missing_ok=True)
        raise RuntimeError("background swap: encode failed")
    return out


# ------------------------------------------------------------------ colour grade
_LUTS = {}
LOOK = {   # S-curve amount, warm lift in the highlights, teal lift in the shadows, saturation
    "warm_cinematic": {"s": 0.18, "warm": 0.035, "teal": 0.03, "sat": 1.08},
    "clean": {"s": 0.10, "warm": 0.012, "teal": 0.0, "sat": 1.04},
}


def _smooth(x, a, b):
    u = np.clip((x - a) / (b - a), 0, 1); return u * u * (3 - 2 * u)


def curves(name):
    """The grade's three channel curves as floats, (256, 3) in BGR order; None for no grade."""
    if name not in LOOK:
        return None
    L = LOOK[name]; x = np.linspace(0, 1, 256)
    s = (1 - L["s"]) * x + L["s"] * _smooth(x, 0, 1)            # gentle S-curve (contrast, soft toe and shoulder)
    hi = _smooth(x, 0.5, 1.0) * (1 - _smooth(x, 0.9, 1.0))       # highlight weight, back to 0 at pure white
    sh = (1 - _smooth(x, 0.0, 0.5)) * _smooth(x, 0.0, 0.12)       # shadow weight, 0 at pure black
    r = s + L["warm"] * hi - 0.6 * L["teal"] * sh                 # warm highlights, a little less red in the shadows
    g = s + 0.35 * L["warm"] * hi + 0.25 * L["teal"] * sh
    b = s - 0.8 * L["warm"] * hi + L["teal"] * sh                 # teal shadows
    return np.stack([b, g, r], 1)


def grade_lut(name):
    """(1, 256, 3) uint8 LUT in BGR order for cv2.LUT, or None for no grade. Every channel curve is monotonic (no banding
    inversions) and keeps black at 0 and white at 255."""
    if name not in LOOK:
        return None
    if name not in _LUTS:
        lut = np.maximum.accumulate(np.clip(curves(name), 0, 1), axis=0)     # belt and braces: never decreasing
        _LUTS[name] = np.round(lut * 255).astype(np.uint8).reshape(1, 256, 3)
    return _LUTS[name]


def apply_grade(bgr, name):
    """The grade on one BGR uint8 frame; name None/unknown returns the frame untouched (the same object)."""
    lut = grade_lut(name)
    if lut is None:
        return bgr
    out = cv2.LUT(bgr, lut)
    sat = LOOK[name]["sat"]
    grey = cv2.cvtColor(cv2.cvtColor(out, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    return cv2.addWeighted(out, sat, grey, 1 - sat, 0)

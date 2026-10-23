"""Spec-driven Reel compositor: footage + component library + captions + branding.

timeline.json (built by timeline.py) drives everything; render a frame range with
    python -m reelworker.media.compose <timeline.json> <f0> <f1> <out.mp4>
"""
import json, math, re, subprocess, sys
import numpy as np, cv2, skia
from .gfx_base import (TF, load_fonts, FPS, W, H, RED, AMBER, CYAN, WHITE, GREEN, clamp, ease_out_expo, ease_out_back,
                       ease_io, prog, col, font, tw, text, glass, chip)

COLORS = {"red": RED, "amber": AMBER, "cyan": CYAN, "green": GREEN, "white": WHITE, "violet": (170, 150, 255)}
def C(name, default=None): return COLORS.get(str(name or "").lower(), AMBER if default is None else default)


# ------------------------------------------------------------------ helpers
def env_alpha(t, g, din=0.4, dout=0.25):
    return ease_out_back(prog(t, g["t0"], din)) * (1 - prog(t, g["t1"] - dout, dout))

def check(c, x, y, s, colr, al):
    p = skia.Path(); p.moveTo(x - s * 0.5, y); p.lineTo(x - s * 0.15, y + s * 0.38); p.lineTo(x + s * 0.55, y - s * 0.42)
    c.drawPath(p, skia.Paint(Color4f=col(colr, al), Style=skia.Paint.kStroke_Style, StrokeWidth=s * 0.16,
                             StrokeCap=skia.Paint.kRound_Cap, StrokeJoin=skia.Paint.kRound_Join, AntiAlias=True))

def cross(c, x, y, s, colr, al):
    p = skia.Paint(Color4f=col(colr, al), StrokeWidth=s * 0.18, StrokeCap=skia.Paint.kRound_Cap, AntiAlias=True)
    c.drawLine(x - s / 2, y - s / 2, x + s / 2, y + s / 2, p); c.drawLine(x + s / 2, y - s / 2, x - s / 2, y + s / 2, p)

def bolt_path(x0, y0, x1, y1, seed, rough=55):
    rng = np.random.default_rng(seed); pts = [(x0, y0)]; n = 14
    for i in range(1, n):
        f = i / n; pts.append((x0 + (x1 - x0) * f + rng.normal(0, rough), y0 + (y1 - y0) * f + rng.normal(0, rough * 0.3)))
    pts.append((x1, y1)); p = skia.Path(); p.moveTo(*pts[0])
    for q in pts[1:]: p.lineTo(*q)
    return p, pts

def draw_bolt(c, t, t0, x0, y0, x1, y1, seed, life=0.4):
    if not (t0 <= t < t0 + life): return
    a = (1 - (t - t0) / life) * (0.6 + 0.4 * (int((t - t0) * 40) % 2))
    p, pts = bolt_path(x0, y0, x1, y1, seed)
    c.drawPath(p, skia.Paint(Color4f=col((170, 150, 255), 0.9 * a), Style=skia.Paint.kStroke_Style, StrokeWidth=26, AntiAlias=True,
                             MaskFilter=skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 22)))
    c.drawPath(p, skia.Paint(Color4f=col(WHITE, a), Style=skia.Paint.kStroke_Style, StrokeWidth=6, AntiAlias=True))
    for k in (4, 8, 11):
        bx, by = pts[k]; q, _ = bolt_path(bx, by, bx + (90 if k % 2 else -110), by + 160, seed + k, 25)
        c.drawPath(q, skia.Paint(Color4f=col(WHITE, 0.7 * a), Style=skia.Paint.kStroke_Style, StrokeWidth=3, AntiAlias=True))

def words(s, n):
    """At most n characters, cut at a word boundary (never shows half a word)."""
    s = " ".join(str(s).split())
    if len(s) <= n: return s
    cut = s[:n + 1].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-") if len(cut) >= n * 0.5 else s[:n]


def fit_font(key, s, size, maxw):
    f = font(key, size)
    while tw(f, s) > maxw and size > 30:
        size -= 6; f = font(key, size)
    return f


# ------------------------------------------------------------------ components
def g_chip(c, t, g):
    p = g["params"]; a = env_alpha(t, g)
    chip(c, words(p.get("text", ""), 42).upper(), W / 2, float(p.get("y", 470)), C(p.get("color"), AMBER), alpha=a,
         dot=bool(p.get("dot", True)), t=t, size=int(p.get("size", 38)))

def g_big_text(c, t, g):
    p = g["params"]; a = env_alpha(t, g, 0.45); y = float(p.get("y", 760)); colr = C(p.get("color"), WHITE)
    size = int(p.get("size", 190)); lines = str(p.get("text", "")).upper().split("\n")[:2]
    for i, ln in enumerate(lines):
        f = fit_font("black", ln, size, 960)
        text(c, ln, W / 2, y + i * size * 0.82, f, colr if i == 0 else C(p.get("color2"), AMBER) if len(lines) > 1 else colr,
             alpha=a, glow=14, scale=0.82 + 0.18 * clamp(a))
    if p.get("sub"):
        chip(c, words(p["sub"], 44).upper(), W / 2, y + (len(lines) - 1) * size * 0.82 + 110, C(p.get("sub_color"), CYAN),
             alpha=a * ease_out_back(prog(t, g["t0"] + 0.35, 0.35)), size=34)

def g_counter(c, t, g):
    p = g["params"]; a = env_alpha(t, g); y = float(p.get("y", 820))
    to = float(p.get("to", 0)); fr = float(p.get("from", 0)); dec = 0 if float(to).is_integer() else 1
    v = fr + (to - fr) * ease_out_expo(prog(t, g["t0"] + 0.1, 0.9))
    s = f"{p.get('prefix', '')}{v:,.{dec}f}{p.get('suffix', '')}"
    text(c, s, W / 2, y, fit_font("black", s, 300, 980), C(p.get("color"), WHITE), alpha=a, glow=10)
    if p.get("label"):
        chip(c, words(p["label"], 44).upper(), W / 2, y + 100, C(p.get("label_color"), RED), alpha=a, dot=True, t=t, size=38)

def g_list_card(c, t, g):
    items = (g["params"].get("items") or [])[:4]; out = prog(t, g["t1"] - 0.3, 0.3)
    times = g.get("reveal") or [g["t0"] + i * 0.35 for i in range(len(items))]
    y0 = float(g["params"].get("y", 540))
    for i, it in enumerate(items):
        ts = times[min(i, len(times) - 1)]
        a = ease_out_expo(prog(t, ts - 0.12, 0.45)) * (1 - out); y = y0 + i * 150
        if a <= 0.01: continue
        ok = it.get("ok"); colr = GREEN if ok is True else RED if ok is False else CYAN
        r = skia.Rect.MakeXYWH(120 + (1 - a) * 90, y - 58, 840, 116)
        glass(c, r, alpha=a, border=colr, border_a=0.75)
        label = words(it.get("text", ""), 44).upper()
        text(c, label, r.left() + 40, y + 20, fit_font("black", label, 60, 700 if ok is not None else 760), WHITE, alpha=a, align="l", shadow=False)
        if ok is True: check(c, r.right() - 70, y, 54, GREEN, a)
        elif ok is False: cross(c, r.right() - 70, y, 50, RED, a)

def g_quote_card(c, t, g):
    p = g["params"]; a = env_alpha(t, g, 0.45); lines = [str(x) for x in (p.get("lines") or [])][:4]
    hgt = 170 + 78 * len(lines) + (60 if p.get("source") else 0); r = skia.Rect.MakeXYWH(90, 500, 900, hgt)
    glass(c, r, alpha=a, border=AMBER, border_a=0.8)
    if p.get("title"):
        qt = words(p["title"], 60).upper(); text(c, qt, W / 2, r.top() + 70, fit_font("psb", qt, 34, 820), AMBER, alpha=a, shadow=False)
    for i, ln in enumerate(lines):
        text(c, ln, W / 2, r.top() + 160 + i * 78, fit_font("black", ln, 70, 820), WHITE, alpha=a, shadow=False)
    if p.get("source"):
        text(c, "— " + words(p["source"], 70), W / 2, r.bottom() - 30, font("pm", 26), (215, 220, 228), alpha=a, shadow=False)

def g_compare(c, t, g):
    p = g["params"]; out = prog(t, g["t1"] - 0.3, 0.3); focus = p.get("focus")
    for i, key in enumerate(("left", "right")):
        side = p.get(key) or {}; colr = C(side.get("color"), RED if i == 0 else AMBER)
        pa = ease_out_expo(prog(t, g["t0"] + i * 0.25, 0.5)); al = pa * (1 - out)
        x = W / 2 + (1 - pa) * (-160 if i == 0 else 160); y = 580 + i * 300          # short slide + fade: never runs off the frame
        on = (focus == key) or (focus is None)
        r = skia.Rect.MakeXYWH(x - 430, y - 125, 860, 250)
        glass(c, r, alpha=al * (1 if on else 0.65), border=colr, border_a=0.85 if on else 0.3)
        c.drawRoundRect(skia.Rect.MakeXYWH(r.left() + 28, r.top() + 30, 10, r.height() - 60), 5, 5, skia.Paint(Color4f=col(colr, al), AntiAlias=True))
        title = words(side.get("title", ""), 30).upper()          # fit_font shrinks it; never cut a word
        text(c, title, r.left() + 70, y + 12, fit_font("black", title, 116, 740), colr if on else WHITE, alpha=al, align="l", shadow=False)
        sub = words(side.get("sub", ""), 60); text(c, sub, r.left() + 72, y + 88, fit_font("pm", sub, 34, 740), WHITE, alpha=al * (0.95 if on else 0.6), align="l", shadow=False)

def g_timer(c, t, g):
    p = g["params"]; a = ease_out_back(prog(t, g["t0"], 0.4)) * (1 - prog(t, g["t1"] + 0.6, 0.4))
    total = float(p.get("seconds", 60)); rem = total * (1 - clamp((t - g["t0"]) / max(0.1, g["t1"] - g["t0"])))
    s = f"{int(rem) // 60}:{int(rem) % 60:02d}"; colr = RED if rem < total * 0.3 else AMBER
    r = skia.Rect.MakeXYWH(W - 330, 205, 260, 112); glass(c, r, rad=22, alpha=a, border=colr, border_a=0.85)
    text(c, words(p.get("label", "TIME LEFT"), 14).upper(), r.centerX(), r.top() + 36, font("psb", 24), (215, 220, 228), alpha=a, shadow=False)
    text(c, s, r.centerX(), r.top() + 96, font("black", 64), RED if rem < total * 0.3 else WHITE, alpha=a, shadow=False)

def g_flash(c, t, g):
    if g["params"].get("bolt", True):
        draw_bolt(c, t, g["t0"] - 0.03, W * 0.42, -40, W * 0.58, 1050, int(g["t0"] * 100) % 97, 0.45)

PLANE = [(0, -1.0), (0.06, -0.9), (0.08, -0.7), (0.08, -0.15), (0.92, 0.18), (0.92, 0.25), (0.08, 0.12), (0.07, 0.62),
         (0.32, 0.82), (0.32, 0.88), (0.05, 0.84), (0.0, 0.95)]
def _plane_pts(cx, cy, s):
    right = [(cx + x * s, cy + y * s) for x, y in PLANE]; left = [(cx - x * s, cy + y * s) for x, y in reversed(PLANE)]
    return right + left
def _poly_point(P, f):
    seg = [math.dist(P[i], P[i + 1]) for i in range(len(P) - 1)]; L = sum(seg) * clamp(f); acc = 0
    for i, d in enumerate(seg):
        if acc + d >= L:
            u = (L - acc) / max(d, 1e-6); return (P[i][0] + (P[i + 1][0] - P[i][0]) * u, P[i][1] + (P[i + 1][1] - P[i][1]) * u)
        acc += d
    return P[-1]

def g_plane_path(c, t, g):
    p = g["params"]; a = ease_out_expo(prog(t, g["t0"], 0.45)) * (1 - prog(t, g["t1"] - 0.3, 0.3))
    c.drawRect(skia.Rect(0, 0, W, H), skia.Paint(Color4f=col((4, 8, 16), 0.6 * a)))
    cx, cy, s = W / 2, 760, 380; P = _plane_pts(cx, cy, s); pp = skia.Path(); pp.moveTo(*P[0])
    for q in P[1:]: pp.lineTo(*q)
    pp.close()
    c.drawPath(pp, skia.Paint(Color4f=col((20, 28, 40), 0.75 * a), AntiAlias=True))
    c.drawPath(pp, skia.Paint(Color4f=col(WHITE, 0.85 * a), Style=skia.Paint.kStroke_Style, StrokeWidth=4, AntiAlias=True))
    half = len(PLANE); right = P[:half]; left = list(reversed(P[half - 1:]))
    k = ease_io(prog(t, g["t0"] + 0.5, max(0.8, (g["t1"] - g["t0"]) * 0.6)))
    for path_, stop in ((right, 1.0), (left, 0.42)):
        for j in range(10):
            f = k * stop - j * 0.025
            if f <= 0: continue
            x, y = _poly_point(path_, f)
            c.drawCircle(x, y, 11 - j * 0.8, skia.Paint(Color4f=col((255, 230, 120), a * (1 - j / 10)), AntiAlias=True,
                                                     MaskFilter=skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 4)))
    draw_bolt(c, t, g["t0"] + 0.45, cx - 60, 230, cx, cy - s + 6, 21, 0.3)
    text(c, str(p.get("entry_label", "ENTRY")).upper()[:12], cx, cy - s - 30, font("black", 56), (190, 170, 255), alpha=a * prog(t, g["t0"] + 0.5, 0.3))
    q = a * prog(t, g["t0"] + 0.5 + (g["t1"] - g["t0"]) * 0.5, 0.3)
    text(c, str(p.get("exit_label", "EXIT")).upper()[:12], cx + 0.32 * s + 20, cy + 0.95 * s + 10, font("black", 60), AMBER, alpha=q, align="l")
    text(c, str(p.get("exit_label", "EXIT")).upper()[:12], cx - 0.92 * s, cy + 0.25 * s + 70, font("black", 48), AMBER, alpha=q)

DRAW = {"chip": g_chip, "big_text": g_big_text, "counter": g_counter, "list_card": g_list_card, "quote_card": g_quote_card,
        "compare": g_compare, "timer": g_timer, "flash": g_flash, "plane_path": g_plane_path}


# ------------------------------------------------------------------ captions, branding
SAFE_W = 900


def draw_captions(c, t, TL):
    if t >= TL["vo_end"] - 0.1: return
    caps = TL["captions"]; starts = sorted(x["t0"] for x in caps)
    for cap in caps:
        a, b, s_ = cap["t0"], cap["t1"], cap["text"]
        nxt = min([st for st in starts if st > a + 1e-6] or [1e9]); b2 = min(b + 0.15, nxt - 0.01)
        if not (a - 0.04 <= t < b2): continue
        f = font("pxb", 62); parts = []
        for m_ in re.finditer(r"\*([^*]+)\*([.,?!]*)|(\S+)", s_):
            parts.append((m_.group(1) + m_.group(2), True) if m_.group(1) else (m_.group(3), False))
        if not parts: continue
        PADH = 56                                               # room for the highlight box (box is 32 wider than the word), so it never covers a neighbour
        widths = [tw(f, p[0]) + (PADH if p[1] else 0) for p in parts]; sp = tw(f, " ") + 12; total = sum(widths) + sp * (len(parts) - 1)
        if total > 1000 and len(parts) > 3:
            acc = 0; k = 1
            for i, w_ in enumerate(widths):
                acc += w_ + sp
                if acc >= total / 2: k = i + 1; break
            lines = [(0, k, 1360), (k, len(parts), 1452)]
        else:
            lines = [(0, len(parts), 1400)]
        seg = max(0.05, (b - a) * 0.6 / max(1, len(parts)))
        for i0, i1, y in lines:
            lw = sum(widths[i0:i1]) + sp * (i1 - i0 - 1); sc = min(1.0, SAFE_W / max(lw, 1))     # side margins clear of Instagram's buttons
            c.save(); c.translate(W / 2, y); c.scale(sc, sc); c.translate(-W / 2, -y); x = W / 2 - lw / 2
            for i in range(i0, i1):
                (wd, k_), w_ = parts[i], widths[i]; ws = a + i * seg
                if t < ws: x += w_ + sp; continue
                p = ease_out_back(prog(t, ws, 0.16), 1.2); cxw = x + w_ / 2      # small pop (max ~4 % bigger) so words never touch while they land
                if k_:
                    w_in = w_ - PADH; pr = skia.Rect.MakeXYWH(cxw - w_in / 2 - 16, y - 62, w_in + 32, 84)
                    c.save(); c.translate(cxw, y - 20); c.scale(p, p); c.translate(-cxw, -(y - 20))
                    c.drawRoundRect(pr, 16, 16, skia.Paint(Color4f=col(AMBER, 0.96), AntiAlias=True)); c.restore()
                    text(c, wd, cxw, y, f, (14, 14, 14), shadow=False, scale=p)
                else:
                    text(c, wd, cxw, y, f, WHITE, alpha=clamp(p), scale=p)
                x += w_ + sp
            c.restore()

def draw_end(c, t, TL, logo):
    ta = TL["vo_end"] - 0.1
    if t < ta - 0.1: return
    p = ease_out_expo(prog(t, ta, 0.6))
    c.drawRect(skia.Rect(0, 0, W, H), skia.Paint(Color4f=col((4, 8, 14), 0.55 * p)))
    lw = 420; lh = lw * logo.height() / logo.width(); y = 640 + (1 - p) * 60
    c.drawImageRect(logo, skia.Rect.MakeXYWH(W / 2 - lw / 2, y, lw, lh), skia.SamplingOptions(skia.FilterMode.kLinear), skia.Paint(Alphaf=p))
    text(c, TL.get("end_title", "FOLLOW FOR MORE"), W / 2, y + lh + 110, font("black", 96), WHITE, alpha=ease_out_expo(prog(t, ta + 0.25, 0.5)))
    text(c, TL.get("end_sub", "aviation facts, explained"), W / 2, y + lh + 175, font("pm", 44), CYAN, alpha=ease_out_expo(prog(t, ta + 0.45, 0.5)))


# ------------------------------------------------------------------ video
class Shot:
    def __init__(s, path):
        s.cap = cv2.VideoCapture(path); s.n = int(s.cap.get(cv2.CAP_PROP_FRAME_COUNT)); s.i = 0; s.last = None
    def get(s, idx):
        idx = max(0, min(idx, s.n - 1))
        while s.i <= idx:
            ok, fr = s.cap.read()
            if not ok: break
            s.last = fr; s.i += 1
        return s.last if s.last is not None else np.zeros((H, W, 3), np.uint8)

def zoomed(fr, z):
    h0, w0 = fr.shape[:2]; ar = W / H
    cw = min(w0, h0 * ar) / z; ch = cw / ar; x0 = (w0 - cw) / 2; y0 = (h0 - ch) / 2
    M = np.float32([[W / cw, 0, -x0 * W / cw], [0, H / ch, -y0 * H / ch]])
    return cv2.warpAffine(fr, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)

def brightness(t, TL):
    k = 1.0
    for g in TL["graphics"]:
        if g["type"] == "flash":
            for d0, s in ((0.0, 1.0), (0.16, 0.6)):
                dt = t - g["t0"] - d0
                if 0 <= dt < 0.22: k += 1.3 * s * float(g["params"].get("strength", 1.0)) * (1 - dt / 0.22) ** 2
        elif g["type"] == "dim" and g["t0"] <= t < g["t1"] + 0.3:
            to = float(g["params"].get("to", 0.35)); k *= 1 - (1 - to) * ease_io(prog(t, g["t0"], max(0.5, (g["t1"] - g["t0"]) * 0.6)))
    return k

def shake(t, TL, fi):
    dx = dy = 0.0
    for g in TL["graphics"]:
        if g["type"] in ("shake", "flash") and 0 <= t - g["t0"] < 0.3:
            k = (1 - (t - g["t0"]) / 0.3) * 14; dx += k * math.sin(fi * 2.1); dy += k * math.cos(fi * 1.7)
    return dx, dy

def encoder_args(final=False):
    enc = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    if "h264_videotoolbox" in enc:
        return ["-c:v", "h264_videotoolbox", "-b:v", "40M", "-allow_sw", "1"]
    return ["-c:v", "libx264", "-preset", "veryfast" if not final else "medium", "-crf", "15"]

def apply_palette(pal):
    """The account's own accent colours (e.g. gold and champagne); We One keeps the original amber/cyan."""
    global AMBER, CYAN
    if pal.get("accent"): AMBER = tuple(pal["accent"]); COLORS["amber"] = AMBER
    if pal.get("secondary"): CYAN = tuple(pal["secondary"]); COLORS["cyan"] = CYAN


def render(TL, f0, f1, out):
    load_fonts(TL["fonts_dir"])
    apply_palette(TL.get("palette") or {})
    shots = {s["id"]: Shot(s["file"]) for s in TL["shots"]}
    logo = skia.Image.open(TL["logo"])
    grain = [np.random.default_rng(i).normal(0, 4.5, (H // 2, W // 2)).astype(np.float32) for i in range(6)]
    yy, xx = np.mgrid[0:H, 0:W]
    vig = (1 - 0.34 * (((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 1.6)) ** 2)).clip(0.5, 1)[..., None].astype(np.float32)
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                            *encoder_args(), "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", out],
                           stdin=subprocess.PIPE)
    surf = skia.Surface(W, H); cuts = [s["t0"] for s in TL["shots"][1:]]
    for fi in range(f0, f1):
        t = fi / FPS
        s = next((s for s in TL["shots"][::-1] if t >= s["t0"]), TL["shots"][0])
        zz = s["z0"] + (s["z1"] - s["z0"]) * clamp((t - s["t0"]) / max(0.01, s["t1"] - s["t0"]))
        img = zoomed(shots[s["id"]].get(int((t - s["t0"]) * FPS)), zz)
        dx, dy = shake(t, TL, fi)
        if dx or dy:
            img = cv2.warpAffine(img, np.float32([[1, 0, dx], [0, 1, dy]]), (W, H), borderMode=cv2.BORDER_REFLECT)
        for ct in cuts:
            d = t - ct
            if -0.1 < d < 0.1:
                k = 1 - abs(d) / 0.1; off = int(230 * k * k) * (1 if d < 0 else -1)
                img = np.roll(img, off, axis=0); img = cv2.blur(img, (1, int(2 + 110 * k * k) | 1))
        f = img.astype(np.float32) * vig * brightness(t, TL) + cv2.resize(grain[fi % 6], (W, H))[..., None]
        rgba = cv2.cvtColor(np.clip(f, 0, 255).astype(np.uint8), cv2.COLOR_BGR2RGBA)
        c = surf.getCanvas(); c.clear(skia.ColorBLACK)
        c.drawImage(skia.Image.fromarray(rgba, colorType=skia.kRGBA_8888_ColorType), 0, 0)
        for g in TL["graphics"]:
            fn = DRAW.get(g["type"])
            if fn and g["t0"] - 0.05 <= t < g["t1"] + 0.6:
                fn(c, t, g)
        if TL["note_range"][0] < t < TL["note_range"][1]:
            a = prog(t, TL["note_range"][0], 0.5) * (1 - prog(t, TL["note_range"][1] - 0.4, 0.4))
            text(c, "Representative visuals", W / 2, 1530, font("psb", 26), (235, 238, 242), alpha=0.8 * a)
        if t < TL["vo_end"] - 0.25:
            lw = 104; lh = lw * logo.height() / logo.width()
            c.drawImageRect(logo, skia.Rect.MakeXYWH(70, 220, lw, lh), skia.SamplingOptions(skia.FilterMode.kLinear), skia.Paint(Alphaf=0.92 * prog(t, 0.3, 0.4)))
        draw_captions(c, t, TL); draw_end(c, t, TL, logo)
        c.drawRect(skia.Rect(0, 0, W * t / TL["end"], 8), skia.Paint(Color4f=col(AMBER, 0.9)))
        enc.stdin.write(surf.toarray(colorType=skia.kRGBA_8888_ColorType).tobytes())
    enc.stdin.close(); enc.wait()
    if enc.returncode != 0:
        raise RuntimeError(f"ffmpeg encode failed for frames {f0}-{f1}")


if __name__ == "__main__":
    TL = json.load(open(sys.argv[1], encoding="utf-8"))
    render(TL, int(sys.argv[2]), int(sys.argv[3]), sys.argv[4])
    print("ok", sys.argv[2], sys.argv[3])

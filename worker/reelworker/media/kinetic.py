"""Kinetic typography for anchor-mode reels (and any reel that opts in): word-by-word captions and the pop-in graphics
kinetic_words, strike, pill_row, arrow_cta, stamp, plus the side-panel end card. Each graphic draws inside g["box"]
(set by anchor.layout so it never touches the presenter's face) and is clipped to it.
Brand look: bold Poppins, yellow #FFC400 + white + navy, soft shadows, safe margins."""
import math
import skia
from .gfx_base import TF, W, H, WHITE, clamp, ease_out_expo, ease_out_back, prog, col, font, tw

YELLOW = (255, 196, 0)
NAVY = (11, 31, 58)
NAVY2 = (20, 52, 96)
INK = (8, 18, 34)
STRIKE_RED = (232, 36, 44)
DEFAULT_BOX = {"kinetic_words": (90, 520, 990, 1160), "pill_row": (70, 1060, 1010, 1240), "stamp": (90, 640, 990, 1080),
               "arrow_cta": (140, 1080, 940, 1300), "strike": (90, 520, 990, 1160)}
PAD = 26                                    # drawing may use this much room around its box (shadows); anchor keeps 40 px


def _box(g):
    b = g.get("box") or DEFAULT_BOX.get(g["type"], (90, 520, 990, 1160))
    return [float(x) for x in b]


def _clip(c, b):
    c.save(); c.clipRect(skia.Rect(b[0] - PAD, b[1] - PAD, b[2] + PAD, b[3] + PAD))


def _fk():
    return "pxb" if "pxb" in TF else "black"


def word(c, s, x, y, f, color, alpha=1.0, stroke=0.0, shadow=0.55, align="c"):
    """Bold text with a soft drop shadow and an optional navy outline (reads on any background)."""
    if alpha <= 0.004:
        return
    w = tw(f, s); x0 = x - w / 2 if align == "c" else (x - w if align == "r" else x)
    if shadow:
        c.drawString(s, x0, y + 7, f, skia.Paint(Color4f=col((0, 0, 0), shadow * alpha), AntiAlias=True,
                                                 MaskFilter=skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 9)))
    if stroke:
        c.drawString(s, x0, y, f, skia.Paint(Color4f=col(INK, 0.9 * alpha), AntiAlias=True, Style=skia.Paint.kStroke_Style,
                                             StrokeWidth=stroke, StrokeJoin=skia.Paint.kRound_Join))
    c.drawString(s, x0, y, f, skia.Paint(Color4f=col(color, alpha), AntiAlias=True))


def rrect(c, r, rad, color, alpha, border=None, bw=0.0, shadow=0.0):
    rr = skia.RRect.MakeRectXY(r, rad, rad)
    if shadow:
        c.drawRRect(skia.RRect.MakeRectXY(r.makeOffset(0, 10), rad, rad), skia.Paint(Color4f=col((0, 0, 0), shadow * alpha), AntiAlias=True,
                    MaskFilter=skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 14)))
    c.drawRRect(rr, skia.Paint(Color4f=col(color, alpha), AntiAlias=True))
    if border and bw:
        c.drawRRect(rr, skia.Paint(Color4f=col(border, alpha), AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=bw))


def fit(key, items, size, maxw, minsize=24):
    while size > minsize and max(tw(font(key, size), s) for s in items) > maxw:
        size -= 2
    return size


# ------------------------------------------------------------------ word-by-word captions
def draw_kcaps(c, t, TL):
    """2-4 words at a time; each word pops in on its own beat (scale-bounce), the word being said is brand yellow, a *keyword*
    sits on a yellow pill. TL["kcaps"] (anchor.chunks + anchor.layout)."""
    if t >= TL["vo_end"] - 0.1:
        return
    for ch in TL.get("kcaps") or []:
        if ch.get("hide") or not (ch["show0"] <= t < ch["show1"]):
            continue
        out = prog(t, ch["show1"] - 0.1, 0.1)                 # gone before the next chunk starts: never two at once
        size = 76; f = font(_fk(), size)
        ws = ch["words"]; widths = [tw(f, w["w"]) + (40 if w["hl"] else 0) for w in ws]
        sp = tw(f, " ") + 26; total = sum(widths) + sp * (len(ws) - 1)
        sc = min(1.0, 900 / max(1.0, total)) * (1 - 0.1 * out)
        y = ch["y"]
        c.save(); c.clipRect(skia.Rect(40, y - 110, W - 40, y + 50))
        c.translate(W / 2, y - 26); c.scale(sc, sc); c.translate(-W / 2, -(y - 26))
        x = W / 2 - total / 2
        for i, w in enumerate(ws):
            ws0 = w["t0"] - 0.05
            if t < ws0:
                x += widths[i] + sp; continue
            k = prog(t, ws0, 0.2); s = 0.5 + 0.5 * ease_out_back(k, 2.6)
            nxt = ws[i + 1]["t0"] - 0.05 if i + 1 < len(ws) else ch["show1"] + 1
            cur = ws0 <= t < nxt
            a = clamp(k * 4) * (1 - out)
            cx = x + widths[i] / 2
            c.save(); c.translate(cx, y - 26); c.scale(s * (1.04 if cur else 1.0), s * (1.04 if cur else 1.0)); c.translate(-cx, -(y - 26))
            if w["hl"]:
                ww = widths[i] - 40
                rrect(c, skia.Rect.MakeXYWH(cx - ww / 2 - 18, y - 66, ww + 36, 88), 18, YELLOW, 0.97 * a, shadow=0.35)
                word(c, w["w"], cx, y, f, NAVY, a, shadow=0)
            else:
                word(c, w["w"], cx, y, f, YELLOW if cur else WHITE, a, stroke=7)
            c.restore()
            x += widths[i] + sp
        c.restore()


# ------------------------------------------------------------------ kinetic words (+ strike)
ROT = (-4.0, 3.5, -2.5, 4.0, -3.0)


def kw_layout(g):
    """[(cx, baseline, size, width, rot)] of each kinetic word inside its box (shared by the strike)."""
    b = _box(g); ws = [str(x).upper() for x in (g.get("params") or {}).get("words") or []]
    if not ws:
        return []
    n = len(ws); bw, bh = b[2] - b[0], b[3] - b[1]
    size = min(150, int(bh / (n * 1.12)))
    size = fit(_fk(), ws, size, bw * 0.9, 30)
    lh = size * 1.12; top = (b[1] + b[3]) / 2 - lh * n / 2
    return [((b[0] + b[2]) / 2, top + lh * i + size * 0.86, size, tw(font(_fk(), size), w), ROT[i % len(ROT)]) for i, w in enumerate(ws)]


def g_kinetic_words(c, t, g):
    """Short words that pop in one by one with a bounce and a slight tilt (DOCTOR? ENGINEER? GRADUATION?)."""
    p = g.get("params") or {}; ws = [str(x).upper() for x in p.get("words") or []]
    if not ws:
        return
    rev = g.get("reveal") or [g["t0"] + 0.35 * i for i in range(len(ws))]
    ex = 1 - prog(t, g["t1"] - 0.25, 0.25)
    if ex <= 0:
        return
    try:
        acc = len(ws) - 1 if p.get("accent") is None else int(p["accent"])
    except Exception:  # noqa: BLE001
        acc = len(ws) - 1
    struck = {int(i): ts for i, ts in g.get("strikes") or []}
    _clip(c, _box(g))
    for i, (cx, y, size, w, rot) in enumerate(kw_layout(g)):
        r = rev[min(i, len(rev) - 1)]
        k = prog(t, r, 0.3)
        if k <= 0:
            continue
        s = (0.25 + 0.75 * ease_out_back(k, 2.2)) * (1 - 0.15 * (1 - ex))
        ang = rot + (1 - ease_out_expo(k)) * (16 if i % 2 else -16)
        a = clamp(k * 3) * ex * (0.5 if i in struck and t >= struck[i] + 0.22 else 1.0)
        f = font(_fk(), size)
        c.save(); c.translate(cx, y - size * 0.35); c.rotate(ang); c.scale(s, s); c.translate(-cx, -(y - size * 0.35))
        word(c, ws[i], cx, y, f, YELLOW if i == acc % len(ws) else WHITE, a, stroke=max(6, size * 0.07))
        c.restore()
    c.restore()


def g_strike(c, t, g):
    """An animated red strike-through over one kinetic word (it draws itself left to right, then stays)."""
    kw = g.get("kwg")
    if not kw:
        return
    lay = kw_layout({"type": "kinetic_words", **kw})
    i = int(g.get("index", 0))
    if not lay or i >= len(lay):
        return
    ex = 1 - prog(t, kw["t1"] - 0.25, 0.25)
    k = ease_out_expo(prog(t, g["t0"], 0.22))
    if k <= 0 or ex <= 0:
        return
    cx, y, size, w, rot = lay[i]; cy = y - size * 0.33; L = w * 1.12
    _clip(c, _box(g))
    c.save(); c.translate(cx, cy); c.rotate(rot - 5)
    pnt = skia.Paint(Color4f=col(STRIKE_RED, ex), StrokeWidth=max(8, size * 0.12), StrokeCap=skia.Paint.kRound_Cap, AntiAlias=True)
    sh = skia.Paint(Color4f=col((0, 0, 0), 0.35 * ex), StrokeWidth=max(8, size * 0.12), StrokeCap=skia.Paint.kRound_Cap, AntiAlias=True,
                    MaskFilter=skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 6))
    c.drawLine(-L / 2, 6, -L / 2 + L * k, 6, sh); c.drawLine(-L / 2, 0, -L / 2 + L * k, 0, pnt)
    c.restore(); c.restore()


# ------------------------------------------------------------------ pill row
def pill_layout(g):
    b = _box(g); its = [str(x) for x in (g.get("params") or {}).get("items") or []]
    if not its:
        return [], 0
    bw, bh = b[2] - b[0], b[3] - b[1]; gap = 20
    for size in range(48, 22, -2):
        f = font("pb", size); ph = size * 1.9; ws = [tw(f, s) + size * 1.5 for s in its]
        if sum(ws) + gap * (len(its) - 1) <= bw * 0.96 and ph <= bh:          # one row
            x = (b[0] + b[2]) / 2 - (sum(ws) + gap * (len(its) - 1)) / 2; cy = (b[1] + b[3]) / 2; out = []
            for w in ws:
                out.append((x + w / 2, cy, w, ph)); x += w + gap
            return out, size
        if max(ws) <= bw * 0.96 and len(its) * ph + gap * (len(its) - 1) <= bh:   # a column
            top = (b[1] + b[3]) / 2 - (len(its) * ph + gap * (len(its) - 1)) / 2
            return [((b[0] + b[2]) / 2, top + i * (ph + gap) + ph / 2, w, ph) for i, w in enumerate(ws)], size
    f = font("pb", 24); ph = 46
    return [((b[0] + b[2]) / 2, b[1] + 30 + i * 56, tw(f, s) + 36, ph) for i, s in enumerate(its)], 24


def g_pill_row(c, t, g):
    """Pills that pop in one after another (Class 12 · Pass-out · Graduation); the newest one is yellow."""
    its = [str(x) for x in (g.get("params") or {}).get("items") or []]
    lay, size = pill_layout(g)
    if not lay:
        return
    rev = g.get("reveal") or [g["t0"] + 0.35 * i for i in range(len(its))]
    ex = 1 - prog(t, g["t1"] - 0.25, 0.25)
    if ex <= 0:
        return
    shown = [i for i in range(len(its)) if t >= rev[min(i, len(rev) - 1)]]
    newest = shown[-1] if shown else -1
    f = font("pb", size)
    _clip(c, _box(g))
    for i, (cx, cy, w, ph) in enumerate(lay):
        k = prog(t, rev[min(i, len(rev) - 1)], 0.3)
        if k <= 0:
            continue
        s = 0.4 + 0.6 * ease_out_back(k, 1.9); a = clamp(k * 3) * ex; dy = (1 - ease_out_expo(k)) * 26
        c.save(); c.translate(cx, cy + dy); c.scale(s, s); c.translate(-cx, -(cy + dy))
        r = skia.Rect.MakeXYWH(cx - w / 2, cy + dy - ph / 2, w, ph)
        hot = i == newest
        rrect(c, r, ph / 2, YELLOW if hot else NAVY, (0.98 if hot else 0.9) * a, border=None if hot else YELLOW, bw=3, shadow=0.4)
        word(c, its[i], cx, cy + dy + size * 0.36, f, NAVY if hot else WHITE, a, shadow=0)
        c.restore()
    c.restore()


# ------------------------------------------------------------------ arrow CTA
def g_arrow_cta(c, t, g):
    """A yellow pill ("Form bhariye") with a bouncing down arrow under it, pointing at the lead form."""
    p = g.get("params") or {}; s_ = str(p.get("text") or "Form bhariye")[:30]
    b = _box(g); bw, bh = b[2] - b[0], b[3] - b[1]
    ex = 1 - prog(t, g["t1"] - 0.25, 0.25); k = prog(t, g["t0"], 0.35)
    if k <= 0 or ex <= 0:
        return
    size = fit(_fk(), [s_], 54, bw * 0.8 - 60, 26); f = font(_fk(), size)
    pw, ph = tw(f, s_) + 70, size * 1.75
    cx = (b[0] + b[2]) / 2; py = b[1] + ph / 2 + 6
    sc = 0.4 + 0.6 * ease_out_back(k, 2.0); a = clamp(k * 3) * ex
    _clip(c, b)
    c.save(); c.translate(cx, py); c.scale(sc, sc); c.translate(-cx, -py)
    rrect(c, skia.Rect.MakeXYWH(cx - pw / 2, py - ph / 2, pw, ph), ph / 2, YELLOW, 0.98 * a, shadow=0.45)
    word(c, s_, cx, py + size * 0.36, f, NAVY, a, shadow=0)
    c.restore()
    room = b[3] - (py + ph / 2) - 12
    if room > 40:
        bounce = abs(math.sin(2 * math.pi * 1.7 * (t - g["t0"]))) * min(18, room * 0.2)
        ah = min(110, room - 20); top = py + ph / 2 + 10 + bounce; sw = max(10, ah * 0.16)
        pth = skia.Path(); pth.moveTo(cx, top); pth.lineTo(cx, top + ah - sw)
        head = skia.Path(); head.moveTo(cx - ah * 0.32, top + ah * 0.58); head.lineTo(cx, top + ah); head.lineTo(cx + ah * 0.32, top + ah * 0.58)
        for pp, colr, wd, blur in ((pth, (0, 0, 0), sw + 4, 6), (head, (0, 0, 0), sw + 4, 6), (pth, YELLOW, sw, 0), (head, YELLOW, sw, 0)):
            pa = skia.Paint(Color4f=col(colr, (0.35 if blur else 1.0) * a), Style=skia.Paint.kStroke_Style, StrokeWidth=wd, AntiAlias=True,
                            StrokeCap=skia.Paint.kRound_Cap, StrokeJoin=skia.Paint.kRound_Join)
            if blur:
                pa.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, blur))
            c.drawPath(pp, pa)
    c.restore()


# ------------------------------------------------------------------ stamp
SLAM = 0.2


def stamp_lines(s_):
    ws = s_.upper().split()
    if len(ws) <= 1:
        return [s_.upper()]
    best = None
    for n in (1, 2, 3):
        if n > len(ws):
            break
        import itertools
        for cuts in itertools.combinations(range(1, len(ws)), n - 1):
            parts = [" ".join(ws[a:b]) for a, b in zip((0,) + cuts, cuts + (len(ws),))]
            score = max(len(p) for p in parts) + 3 * n
            if best is None or score < best[0]:
                best = (score, parts)
    return best[1]


def g_stamp(c, t, g):
    """Big text that slams in with a shake (FREE CAREER COUNSELLING): navy panel, yellow border, first word in yellow."""
    p = g.get("params") or {}; s_ = str(p.get("text") or "")[:40]
    if not s_:
        return
    b = _box(g); bw, bh = b[2] - b[0], b[3] - b[1]
    k = prog(t, g["t0"], SLAM); ex = 1 - prog(t, g["t1"] - 0.25, 0.25)
    if k <= 0 or ex <= 0:
        return
    lines = stamp_lines(s_); rot = float(p.get("rot", -5))
    size = 120
    while size > 28:
        f = font(_fk(), size); pw = max(tw(f, ln) for ln in lines) + size * 1.1; ph = len(lines) * size * 1.05 + size * 0.75
        ra = math.radians(abs(rot)); rw = pw * math.cos(ra) + ph * math.sin(ra); rh = pw * math.sin(ra) + ph * math.cos(ra)
        if rw <= bw * 0.94 and rh <= bh * 0.94:
            break
        size -= 4
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    land = t - g["t0"] - SLAM
    sc = (2.3 - 1.3 * k * k) if k < 1 else 1 + 0.05 * math.exp(-land * 10) * math.cos(land * 38)
    a = clamp(k * 2.2) * ex
    _clip(c, b)
    if land >= 0 and land < 0.4:                       # the impact ring
        rr = (0.4 + 1.2 * ease_out_expo(land / 0.4)) * max(pw, ph) * 0.5
        c.drawCircle(cx, cy, rr, skia.Paint(Color4f=col(YELLOW, 0.5 * (1 - land / 0.4) * ex), Style=skia.Paint.kStroke_Style, StrokeWidth=8, AntiAlias=True))
    c.save(); c.translate(cx, cy); c.rotate(rot); c.scale(sc, sc)
    r = skia.Rect.MakeXYWH(-pw / 2, -ph / 2, pw, ph)
    rrect(c, r, 26, NAVY, 0.94 * a, border=YELLOW, bw=max(6, size * 0.08), shadow=0.5)
    f = font(_fk(), size)
    y = -ph / 2 + size * 0.38 + size * 0.86
    for i, ln in enumerate(lines):
        if i == 0 and len(lines) > 1:
            word(c, ln, 0, y, f, YELLOW, a, shadow=0)
        else:
            first, _, rest = ln.partition(" ")
            if i == 0 and rest:                       # one line: the first word yellow, the rest white
                wf, wr = tw(f, first + " "), tw(f, rest); x0 = -(wf + wr) / 2
                word(c, first, x0, y, f, YELLOW, a, shadow=0, align="l"); word(c, rest, x0 + wf, y, f, WHITE, a, shadow=0, align="l")
            else:
                word(c, ln, 0, y, f, WHITE, a, shadow=0)
        y += size * 1.05
    c.restore(); c.restore()


def stamp_land(g):
    return g["t0"] + SLAM


DRAW = {"kinetic_words": g_kinetic_words, "strike": g_strike, "pill_row": g_pill_row, "arrow_cta": g_arrow_cta, "stamp": g_stamp}


# ------------------------------------------------------------------ end card beside the presenter
def wrap(s_, f, maxw):
    lines, cur = [], ""
    for w in str(s_).split():
        nxt = (cur + " " + w).strip()
        if cur and tw(f, nxt) > maxw:
            lines.append(cur); cur = w
        else:
            cur = nxt
    return lines + ([cur] if cur else [])


def draw_end_panel(c, t, TL, logo):
    """She has moved aside (anchor_render shifts the picture); the logo + offer panel slides in on the free side."""
    e = TL["anchor"]["end"]; ta = TL["vo_end"] - 0.1
    if t < ta - 0.02:
        return
    k = ease_out_expo(prog(t, ta, 0.6)); right = e["panel"] == "right"
    x0 = e["x0"]; g0 = x0 - 0.04 * W if right else x0 + 0.04 * W
    fx = e.get("fx", g0 - e["dx"]) + e["dx"] * k                  # her face edge right now: the shade never reaches it
    g0 = max(g0, fx) if right else min(g0, fx)
    pts = [skia.Point(g0, 0), skia.Point(g0 + (0.18 * W if right else -0.18 * W), 0)]
    sh = skia.GradientShader.MakeLinear(pts, [skia.ColorSetARGB(0, *INK), skia.ColorSetARGB(int(255 * 0.93 * k), *INK)])
    c.drawRect(skia.Rect(g0, 0, W, H) if right else skia.Rect(0, 0, g0, H), skia.Paint(Shader=sh))
    px0, px1 = (x0 + 24, W - 64) if right else (64, x0 - 24)
    pw = px1 - px0; slide = (1 - k) * (260 if right else -260)
    cx = (px0 + px1) / 2 + slide; y = 600
    if logo is not None:
        lw = min(300.0, pw * 0.72); lh = lw * logo.height() / logo.width()
        c.drawImageRect(logo, skia.Rect.MakeXYWH(cx - lw / 2, y, lw, lh), skia.SamplingOptions(skia.FilterMode.kLinear), skia.Paint(Alphaf=k))
        y += lh + 60
    c.drawRoundRect(skia.Rect.MakeXYWH(cx - 45, y, 90, 7), 3.5, 3.5, skia.Paint(Color4f=col(YELLOW, k), AntiAlias=True)); y += 40
    title = str(TL.get("end_title") or "FOLLOW FOR MORE").upper().split()
    size = fit(_fk(), title, 86, pw * 0.96, 30); f = font(_fk(), size)
    a1 = ease_out_expo(prog(t, ta + 0.15, 0.5))
    for i, ln in enumerate(wrap(" ".join(title), f, pw * 0.96)):
        y += size * 1.02
        word(c, ln, cx, y, f, YELLOW if i == 0 else WHITE, a1, shadow=0.4)
    sub = str(TL.get("end_sub") or "")
    if sub:
        fs = font("pm", 32); y += 30
        for ln in wrap(sub, fs, pw * 0.96)[:3]:
            y += 44; word(c, ln, cx, y, fs, (214, 222, 235), ease_out_expo(prog(t, ta + 0.3, 0.5)), shadow=0.3)
    if TL.get("end_cta"):
        a3 = ease_out_expo(prog(t, ta + 0.45, 0.4)); s_ = str(TL["end_cta"])[:28]
        fc = font(_fk(), fit(_fk(), [s_], 44, pw * 0.8, 24)); cw = tw(fc, s_) + 60; ch = 76; y += 70
        bounce = abs(math.sin(2 * math.pi * 1.6 * t)) * 8
        rrect(c, skia.Rect.MakeXYWH(cx - cw / 2, y - bounce, cw, ch), ch / 2, YELLOW, 0.98 * a3, shadow=0.4)
        word(c, s_, cx, y - bounce + ch / 2 + fc.getSize() * 0.36, fc, NAVY, a3, shadow=0)

# Shared helpers for EduReelStudio skia/cv2 reels
import math, numpy as np, cv2, skia
FPS = 60; W, H = 1080, 1920
TF = {}
def load_fonts(F):
    from pathlib import Path
    want = {"black": "BarlowCondensed-Black.ttf", "xb": "BarlowCondensed-ExtraBold.ttf", "bc": "BarlowCondensed-Bold.ttf",
            "pb": "Poppins-Bold.ttf", "pxb": "Poppins-ExtraBold.ttf", "psb": "Poppins-SemiBold.ttf", "pm": "Poppins-Medium.ttf",
            "mono": "JetBrainsMono[wght].ttf"}
    found = {p.name: p for p in Path(F).rglob("*.ttf")}
    for k, v in want.items():
        if v not in found:
            raise FileNotFoundError(f"font {v} not found under {F}")
        TF[k] = skia.Typeface.MakeFromFile(str(found[v]))
RED, AMBER, CYAN, WHITE, GREEN = (255, 59, 48), (255, 176, 32), (56, 225, 255), (255, 255, 255), (52, 211, 120)
def clamp(x, a=0, b=1): return max(a, min(b, x))
def ease_out_expo(x): x = clamp(x); return 1 if x == 1 else 1 - 2 ** (-10 * x)
def ease_out_back(x, s=1.7): x = clamp(x) - 1; return 1 + (s + 1) * x ** 3 + s * x ** 2
def ease_io(x): x = clamp(x); return 4 * x ** 3 if x < .5 else 1 - (-2 * x + 2) ** 3 / 2
def prog(t, a, d): return clamp((t - a) / d)
def col(c, a=1.0): return skia.Color4f(c[0] / 255, c[1] / 255, c[2] / 255, clamp(a))
def font(k, s): f = skia.Font(TF[k], s); f.setEdging(skia.Font.Edging.kSubpixelAntiAlias); f.setSubpixel(True); return f
def tw(f, s): return f.measureText(s)

def text(c, s, x, y, f, color, alpha=1, align="c", glow=0, shadow=True, scale=1.0):
    w = tw(f, s); x0 = x - w / 2 if align == "c" else (x - w if align == "r" else x)
    c.save(); c.translate(x, y); c.scale(scale, scale); c.translate(-x, -y)
    if shadow:
        p = skia.Paint(Color4f=col((0, 0, 0), 0.55 * alpha), MaskFilter=skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 10))
        c.drawString(s, x0, y + 6, f, p)
    if glow:
        p = skia.Paint(Color4f=col(color, 0.8 * alpha), MaskFilter=skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, glow))
        c.drawString(s, x0, y, f, p)
    c.drawString(s, x0, y, f, skia.Paint(Color4f=col(color, alpha), AntiAlias=True))
    c.restore(); return w

def glass(c, r, rad=34, alpha=1.0, tint=(14, 18, 26), border=(255, 255, 255), border_a=0.18, blur=28):
    if alpha < 0.03: return
    blur = max(1, blur * clamp(alpha)); rr = skia.RRect.MakeRectXY(r, rad, rad)
    c.save(); c.clipRRect(rr, True)
    flt = skia.ImageFilters.Blur(blur, blur); rec = skia.Canvas.SaveLayerRec(r, None, flt, 0)
    c.saveLayer(rec); c.restore(); del rec, flt
    c.drawRRect(rr, skia.Paint(Color4f=col(tint, 0.62 * alpha)))
    c.restore()
    c.drawRRect(rr, skia.Paint(Color4f=col(border, border_a * alpha), Style=skia.Paint.kStroke_Style, StrokeWidth=2, AntiAlias=True))

def chip(c, s, x, y, color, alpha=1, dot=False, t=0, size=34):
    f = font("psb", size); w = tw(f, s) + (70 if dot else 48); h = size * 1.75
    r = skia.Rect.MakeXYWH(x - w / 2, y - h / 2, w, h)
    glass(c, r, rad=h / 2, alpha=alpha, border=color, border_a=0.55)
    tx = x - w / 2 + (52 if dot else 24)
    if dot:
        blink = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(t * 2 * math.pi * 2.2))
        c.drawCircle(x - w / 2 + 30, y, 9, skia.Paint(Color4f=col(color, alpha * blink), AntiAlias=True))
    c.drawString(s, tx, y + size * 0.36, f, skia.Paint(Color4f=col(WHITE, alpha), AntiAlias=True))


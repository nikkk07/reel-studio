"""Still pictures with skia: Instagram carousel slides (1080x1350) and Meta ad pictures (1080x1350 / 1080x1920 / 1080x1080).
Same fonts and palette as the reels. Text is measured and wrapped so it always fits; a background frame from our own 3D clips
is darkened under the text when one is given."""
from pathlib import Path
import skia
from . import gfx_base as G

NAVY_TOP, NAVY_BOT = (6, 12, 26), (16, 34, 66)
MARGIN = 84


_FALLBACK = {}


def _face_for(ch, key):
    """The reel font when it has the character, else Poppins (has ₹), else whatever system font has it (arrows, symbols)."""
    tf = G.TF[key]
    if tf.unicharToGlyph(ord(ch)) or ch.isspace():
        return tf
    if ch not in _FALLBACK:
        alt = next((G.TF[k] for k in ("pxb", "pb", "pm") if k in G.TF and G.TF[k].unicharToGlyph(ord(ch))), None)
        _FALLBACK[ch] = alt or skia.FontMgr.RefDefault().matchFamilyStyleCharacter("", skia.FontStyle(), ["en"], ord(ch)) or tf
    return _FALLBACK[ch]


def _runs(text, key):
    out = []
    for ch in str(text):
        tf = _face_for(ch, key)
        if out and out[-1][0] is tf:
            out[-1][1] += ch
        else:
            out.append([tf, ch])
    return out


def _font(tf, size):
    f = skia.Font(tf, size); f.setEdging(skia.Font.Edging.kSubpixelAntiAlias); f.setSubpixel(True); return f


def width_of(text, f):
    key = next((k for k, tf in G.TF.items() if tf is f.getTypeface()), None)
    if key is None:
        return f.measureText(text)
    return sum(_font(tf, f.getSize()).measureText(t) for tf, t in _runs(text, key))


def draw(c, text, x, y, f, color, align="l", alpha=1.0):
    """Text with per-character font fallback, so ₹, → and other symbols never show as empty boxes."""
    key = next((k for k, tf in G.TF.items() if tf is f.getTypeface()), None)
    runs = _runs(text, key) if key else [[f.getTypeface(), text]]
    w = sum(_font(tf, f.getSize()).measureText(t) for tf, t in runs)
    x0 = x - w / 2 if align == "c" else x - w if align == "r" else x
    paint = skia.Paint(Color4f=G.col(color, alpha), AntiAlias=True)
    for tf, t in runs:
        ft = _font(tf, f.getSize()); c.drawString(t, x0, y, ft, paint); x0 += ft.measureText(t)
    return w


def _wrap(text, f, width):
    lines, cur = [], ""
    for w in str(text or "").split():
        t = f"{cur} {w}".strip()
        if width_of(t, f) <= width or not cur:
            cur = t
        else:
            lines.append(cur); cur = w
    if cur:
        lines.append(cur)
    return lines


def fit(text, key, size, width, max_lines, min_size=30):
    """Largest font (from size down) that fits the text in max_lines lines of width. Returns (font, lines, size)."""
    s = size
    while True:
        f = G.font(key, s); lines = _wrap(text, f, width)
        if len(lines) <= max_lines or s <= min_size:
            if len(lines) > max_lines:             # still too long at the smallest size: cut with an ellipsis
                lines = lines[:max_lines]; lines[-1] = lines[-1].rstrip(".,;:") + "…"
            return f, lines, s
        s -= 4


def _block(c, text, key, size, x, y, width, max_lines, color, lh=1.12, min_size=30, align="l"):
    f, lines, s = fit(text, key, size, width, max_lines, min_size)
    for i, ln in enumerate(lines):
        draw(c, ln, x + (width / 2 if align == "c" else 0), y + s + i * s * lh, f, color, align=align)
    return y + len(lines) * s * lh


def _background(c, W, H, bg=None):
    p = skia.Paint(Shader=skia.GradientShader.MakeLinear([(0, 0), (0, H)], [G.col(NAVY_TOP), G.col(NAVY_BOT)]))
    c.drawRect(skia.Rect.MakeWH(W, H), p)
    if bg and Path(bg).exists():
        img = skia.Image.open(str(bg))
        sc = max(W / img.width(), H / img.height()); w, h = img.width() * sc, img.height() * sc
        c.drawImageRect(img, skia.Rect.MakeXYWH((W - w) / 2, (H - h) / 2, w, h), skia.SamplingOptions(skia.FilterMode.kLinear))
        shade = skia.Paint(Shader=skia.GradientShader.MakeLinear([(0, 0), (0, H)], [G.col((4, 8, 18), 0.45), G.col((4, 8, 18), 0.92)]))
        c.drawRect(skia.Rect.MakeWH(W, H), shade)
    # a faint flight path: the brand's motif, never over the text column
    path = skia.Path(); path.moveTo(W * 0.62, H * 0.08); path.cubicTo(W * 0.95, H * 0.15, W * 0.78, H * 0.32, W * 1.02, H * 0.38)
    c.drawPath(path, skia.Paint(Color4f=G.col(G.AMBER, 0.35), Style=skia.Paint.kStroke_Style, StrokeWidth=4, AntiAlias=True,
                                PathEffect=skia.DashPathEffect.Make([18, 14], 0)))


def _logo(c, logo, W):
    if logo and Path(logo).exists():
        img = skia.Image.open(str(logo)); h = 72; w = img.width() * h / img.height()
        c.drawImageRect(img, skia.Rect.MakeXYWH(MARGIN, 64, w, h), skia.SamplingOptions(skia.FilterMode.kLinear))


def _save(surface, out, quality=92):
    out = Path(out); out.parent.mkdir(parents=True, exist_ok=True)
    img = surface.makeImageSnapshot()
    data = img.encodeToData(skia.kJPEG, quality) if out.suffix.lower() in (".jpg", ".jpeg") else img.encodeToData()
    out.write_bytes(bytes(data))
    return out


def slide(s, i, n, out, fonts_dir, logo=None, handle="", bg=None, W=1080, H=1350):
    """One carousel slide. s = {kind, headline, body, source?}; i is 1-based."""
    if not G.TF:
        G.load_fonts(fonts_dir)
    surf = skia.Surface(W, H); c = surf.getCanvas()
    _background(c, W, H, bg if s.get("kind") == "cover" else None)
    _logo(c, logo, W)
    G.chip(c, f"{i}/{n}", W - MARGIN - 50, 100, G.AMBER, size=30)
    kind, width = s.get("kind", "point"), W - 2 * MARGIN
    if kind == "cover":
        y = _block(c, s["headline"].upper(), "black", 150, MARGIN, H * 0.30, width, 4, G.WHITE, lh=1.0, min_size=78)
        c.drawRect(skia.Rect.MakeXYWH(MARGIN, y + 34, 140, 10), skia.Paint(Color4f=G.col(G.AMBER)))
        _block(c, s.get("body", ""), "pm", 46, MARGIN, y + 70, width, 3, (220, 228, 240))
        draw(c, "Swipe  →", W - MARGIN, H - 86, G.font("psb", 38), G.AMBER, align="r")
    elif kind == "cta":
        y = _block(c, s["headline"], "black", 120, MARGIN, H * 0.28, width, 4, G.WHITE, lh=1.02, min_size=70)
        y = _block(c, s.get("body", ""), "pm", 46, MARGIN, y + 50, width, 4, (220, 228, 240))
        if handle:
            G.chip(c, handle, W / 2, min(H - 150, y + 150), G.CYAN, size=40)
    else:
        num = G.font("black", 220)
        G.text(c, f"{i - 1:02d}", MARGIN, 420, num, G.AMBER, alpha=0.9, align="l", shadow=False)
        y = _block(c, s["headline"], "pxb", 74, MARGIN, 470, width, 3, G.WHITE, lh=1.1, min_size=44)
        _block(c, s.get("body", ""), "pm", 44, MARGIN, y + 40, width, 8, (214, 222, 236), lh=1.3, min_size=32)
        if s.get("source"):
            draw(c, f"Source: {s['source']}"[:70], MARGIN, H - 86, G.font("pm", 30), (150, 165, 190))
    return _save(surf, out)


def carousel(slides, folder, fonts_dir, logo=None, handle="", cover_bg=None):
    n = len(slides)
    return [slide(s, i, n, Path(folder) / f"slide_{i:02d}.jpg", fonts_dir, logo, handle, cover_bg) for i, s in enumerate(slides, 1)]


def ad_picture(words, size, out, fonts_dir, logo=None, bg=None):
    """A Meta ad picture: badge, headline, one proof line and a button. size like '1080x1350'."""
    if not G.TF:
        G.load_fonts(fonts_dir)
    W, H = (int(x) for x in size.split("x"))
    surf = skia.Surface(W, H); c = surf.getCanvas()
    _background(c, W, H, bg)
    _logo(c, logo, W)
    width = W - 2 * MARGIN
    k = min(1.3, max(1.0, H / 1350))                  # Stories/Reels (1080x1920): larger type, kept inside the safe zone
    top = H * (0.30 if H > 1500 else 0.34 if H > 1100 else 0.26)
    if words.get("badge"):
        b = words["badge"][:24]; bf = G.font("psb", int(34 * k)); bw = width_of(b, bf) + 48; bh = 34 * k * 1.75
        r = skia.Rect.MakeXYWH(MARGIN, top - 70 * k - bh / 2, bw, bh)
        G.glass(c, r, rad=bh / 2, border=G.AMBER, border_a=0.55)
        draw(c, b, MARGIN + 24, top - 70 * k + 34 * k * 0.36, bf, G.WHITE)
    y = _block(c, words["headline"], "black", int(130 * k), MARGIN, top, width, 3, G.WHITE, lh=1.0, min_size=70)
    y = _block(c, words.get("sub", ""), "pm", int(48 * k), MARGIN, y + 36 * k, width, 3, (220, 228, 240), lh=1.25)
    cta = (words.get("cta") or "Learn more")[:20]
    f = G.font("pb", int(44 * k)); bw = width_of(cta, f) + 96 * k; bh = 104 * k; by = min(H * (0.78 if H > 1500 else 0.86) - bh, y + 80 * k)
    r = skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(MARGIN, by, bw, bh), bh / 2, bh / 2)
    c.drawRRect(r, skia.Paint(Color4f=G.col(G.AMBER), AntiAlias=True))
    draw(c, cta, MARGIN + bw / 2, by + bh * 0.65, f, (12, 16, 28), align="c")
    return _save(surf, out)

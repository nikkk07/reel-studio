"""Frame renderer for anchor-mode timelines (TL["anchor"], built by anchor.py): the presenter clip is the base layer, cut
out with her person mask and placed over each scene's background (stock clips rotating every 2-3 s, or a still with a slow
push-in), short full-screen cutaways in between, whip / zoom-blur transitions, kinetic captions and graphics laid out around
her face, and the side-panel end card. compose.render() hands an anchor timeline to render() here; every other timeline
never comes near this file."""
import math, subprocess
from pathlib import Path
import numpy as np, cv2, skia
from .gfx_base import load_fonts, FPS, W, H, col, clamp, prog
from . import compose, proedit, anchor, kinetic

IMAGES = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
DOF = 6.0                     # depth blur of the plate behind her (px on the 1296 canvas)


TAIL_S = 3.0


def make_tail(src, out, seconds=TAIL_S):
    """The last seconds of a clip, reversed, so the presenter can play back past her clip's end (ping-pong) while the end
    card shows: read forward, frame-exact, never a frozen frame. None if it cannot be made."""
    out = Path(out)
    if out.exists() and out.stat().st_size > 0 and out.stat().st_mtime >= Path(src).stat().st_mtime:
        return out
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-sseof", f"-{seconds:.2f}", "-i", str(src), "-vf", "reverse", "-an",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p", str(out)], capture_output=True)
    return out if r.returncode == 0 and out.exists() else None


class Media:
    """A clip or a still, read by time. Video frames are read forward and seek only on jumps."""
    def __init__(self, path, gray=False, tail=None):
        self.path, self.gray, self.img = str(path), gray, None
        self.tail = Media(tail, gray) if tail and Path(tail).exists() else None
        if self.path.lower().endswith(IMAGES):
            im = cv2.imread(self.path, cv2.IMREAD_UNCHANGED)
            if im is None:
                raise RuntimeError(f"cannot read {self.path}")
            if gray:
                im = im if im.ndim == 2 else (im[..., 3] if im.shape[2] == 4 else im[..., 0])
            elif im.ndim == 2:
                im = cv2.cvtColor(im, cv2.COLOR_GRAY2BGR)
            elif im.shape[2] == 4:
                im = im[..., :3]
            self.img, self.fps, self.n, self.dur = im, FPS, 1, float("inf")
        else:
            self.cap = cv2.VideoCapture(self.path)
            self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
            self.n = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT)); self.dur = self.n / self.fps; self.i = 0; self.last = None

    def frame(self, idx):
        if self.img is not None:
            return self.img
        idx = max(0, min(int(idx), self.n - 1))
        if self.last is not None and idx == self.i - 1:
            out = self.last
        else:
            if idx < self.i - 1 or idx > self.i + int(self.fps * 2):
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, idx); self.i = idx
            while self.i <= idx:
                ok, fr = self.cap.read()
                if not ok:
                    break
                self.last = fr; self.i += 1
            out = self.last if self.last is not None else np.zeros((H, W, 3), np.uint8)
        return out[..., 0] if self.gray and out.ndim == 3 else out

    def at(self, sec, bounce=False):
        if self.img is not None:
            return self.img
        last = self.dur - 1.0 / self.fps
        if bounce and sec > last and last > 0.5:               # past the end: play back (ping-pong), never a frozen frame
            over = sec - last
            if self.tail is not None and self.tail.dur > 0.5:
                td = self.tail.dur - 1.0 / self.tail.fps; u = over % (2 * td)
                return self.tail.at(u) if u < td else self.frame(int((last - td + (u - td)) * self.fps + 1e-6))
            sec = max(0.0, last - over)
        return self.frame(int(max(0.0, sec) * self.fps + 1e-6))


def cover(fr):
    return compose.zoomed(fr, 1.0) if fr.shape[:2] != (H, W) else fr


def place(person, mask, scale, dx):
    """The cut-out presenter scaled about the bottom centre (she stands on the frame edge) and moved sideways."""
    if scale == 1.0 and dx == 0:
        return person, mask
    M = np.float32([[scale, 0, (1 - scale) * W / 2 + dx], [0, scale, (1 - scale) * H]])
    return (cv2.warpAffine(person, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE),
            cv2.warpAffine(mask, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0))


def composite(person, mask, pl, P=None, strength=0.3, wrap=0.65):
    """Presenter over a prepared plate: feathered edge, gentle colour match, 1-2 px light wrap (as proedit.composite).
    Returns (frame, alpha 0-1)."""
    a = proedit.feather(mask)
    fg = proedit.color_match(person, P, strength).astype(np.float32)
    plf = pl.astype(np.float32)
    out = fg * a[..., None] + plf * (1 - a[..., None])
    glow = cv2.GaussianBlur(plf, (0, 0), proedit._px(5, W))
    lw = np.clip(cv2.GaussianBlur(1 - a, (0, 0), proedit._px(1.5, W)) * a, 0, 1) * wrap
    out += lw[..., None] * (glow - out)
    return np.clip(out + 0.5, 0, 255).astype(np.uint8), a


def zoom_frame(img, z, border=cv2.BORDER_REFLECT):
    if abs(z - 1.0) < 1e-4:
        return img
    M = np.float32([[z, 0, (1 - z) * W / 2], [0, z, (1 - z) * H / 2]])
    return cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=border, borderValue=0)


def transition(img, mask, t, TL):
    """Whip-pan (horizontal motion blur sweep) or zoom-blur around each TL["transitions"] point (0.25 s)."""
    for tr in TL.get("transitions") or []:
        d = t - tr["t"]; half = tr.get("d", anchor.TRANS) / 2
        if not -half < d < half:
            continue
        k = 1 - abs(d) / half; k2 = k * k
        if tr["kind"] == "whip":
            off = int(W * 0.45 * k2) * (-1 if d < 0 else 1)
            img = cv2.blur(np.roll(img, off, axis=1), (int(3 + 150 * k2) | 1, 1))
            if mask is not None:
                mask = np.roll(mask, off, axis=1)
        else:
            z = 1 + 0.12 * k2
            acc = np.zeros(img.shape, np.float32)
            for j in range(4):
                acc += zoom_frame(img, z + 0.03 * k * j).astype(np.float32)
            img = np.clip(acc / 4 * (1 + 0.12 * k2), 0, 255).astype(np.uint8)
            if mask is not None:
                mask = zoom_frame(mask, z, cv2.BORDER_CONSTANT)
    return img, mask


def shake(t, TL, fi):
    return compose.shake(t, TL, fi)


class Frames:
    """Everything that turns a time into the picture (no text): presenter, scenes, cutaways, geometry."""
    def __init__(self, TL):
        A = TL["anchor"]; self.TL = TL
        self.clip = Media(A["clip"], tail=A.get("tail")) if A.get("clip") else None
        self.matte = Media(A["matte"], gray=True, tail=A.get("matte_tail")) if A.get("matte") and Path(A["matte"]).exists() else None
        self.files, self.P = {}, {}

    def media(self, path):
        if path not in self.files:
            self.files[path] = Media(path)
        return self.files[path]

    def plate(self, sc, t):
        """The scene's background at t (None without one)."""
        bg = sc.get("bg") or {}
        if sc.get("plates"):
            p = next((p for p in sc["plates"] if p["t0"] <= t < p["t1"]), sc["plates"][-1] if t >= sc["plates"][-1]["t0"] else sc["plates"][0])
            fr = self.media(p["file"]).at(max(0.0, t - p["t0"]) + p.get("off", 0.0))
            k = (t - p["t0"]) / max(0.01, p["t1"] - p["t0"])
            return proedit.plate(fr, k, (H, W), z1=1.05, dof=DOF)
        f = bg.get("file") or bg.get("image")
        if f and Path(f).exists():
            k = (t - sc["t0"]) / max(0.01, sc["t1"] - sc["t0"])
            return proedit.plate(self.media(f).at(0), k, (H, W), z1=1.07, dof=DOF)
        return None

    def presenter(self, s, t):
        """(person frame, mask or None) on the canvas, before the scene."""
        ct = max(0.0, s["clip_t0"] + (t - s["vo_t0"]))
        if self.clip is None:
            return np.zeros((H, W, 3), np.uint8), None
        fr = cover(self.clip.at(ct, bounce=True))
        m = cover(self.matte.at(ct, bounce=True)) if self.matte is not None else None
        return fr, m

    def params(self, sc):
        """One colour-match transform per scene, from the scene's first presenter frame (identical in every render chunk)."""
        if sc["i"] not in self.P:
            t = next((max(sc["t0"], s["t0"]) for s in self.TL["shots"] if s["kind"] == "anchor" and s["t1"] > sc["t0"] and s["t0"] < sc["t1"]), sc["t0"])
            s = anchor.seg_at(self.TL, t)
            P = None
            if s and s["kind"] == "anchor" and self.matte is not None:
                tmp = Frames(self.TL); fr, m = tmp.presenter(s, t); pl = tmp.plate(sc, t)
                if pl is not None:
                    fr, m = place(fr, m, sc["scale"], anchor.POS_DX[sc["pos"]] * W)
                    P = proedit.match_params([fr], [m], [pl])
            self.P[sc["i"]] = P
        return self.P[sc["i"]]

    def at(self, t, fi=0):
        """(BGR frame, person alpha 0-255 or None, segment)."""
        TL = self.TL; s = anchor.seg_at(TL, t); mask = None
        if s["kind"] == "anchor":
            img, m = self.presenter(s, t)
            sc = anchor.scene_at(TL, t) if m is not None else None
            pl = self.plate(sc, t) if sc else None
            if pl is not None:
                person, m2 = place(img, m, sc["scale"], anchor.POS_DX[sc["pos"]] * W)
                img, a = composite(person, m2, pl, self.params(sc))
                mask = (a * 255).astype(np.uint8)
            elif m is not None:
                mask = m
        else:
            img = cover(self.media(s["file"]).at(max(0.0, t - s["t0"]) + s.get("off", 0.0)))
        z = anchor.zoom_at(s, t)
        img = zoom_frame(img, z)
        if mask is not None:
            mask = zoom_frame(mask, z, cv2.BORDER_CONSTANT)
        dx, dy = shake(t, TL, fi); dx += anchor.end_shift(TL, t) if s["kind"] == "anchor" else 0.0
        if dx or dy:
            M = np.float32([[1, 0, dx], [0, 1, dy]])
            img = cv2.warpAffine(img, M, (W, H), borderMode=cv2.BORDER_REFLECT)
            if mask is not None:
                mask = cv2.warpAffine(mask, M, (W, H), borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        img, mask = transition(img, mask, t, TL)
        return img, mask, s


LOGO = (70, 220, 104)


def logo_box(logo):
    x, y, w = LOGO; return (x, y, x + w, y + w * logo.height() / logo.width())


def draw_overlays(c, t, TL, s, logo):
    """Every graphic in front of the picture, the captions, the logo and the end card. Never covers her face: positions come
    from anchor.layout; centre cards are left out while she is on screen."""
    pres = s is not None and s["kind"] == "anchor"
    for g in TL["graphics"]:
        if g.get("hide") or g["type"] == "behind_text" or not (g["t0"] - 0.05 <= t < g["t1"] + (1.0 if g["type"] == "timer" else 0.05)):
            continue
        if pres and g["type"] in anchor.CENTER:
            continue
        fn = compose.DRAW.get(g["type"])
        if fn:
            fn(c, t, g)
    if TL.get("ai_label"):
        compose.ai_label(c, TL["ai_label"])
    end = (TL["anchor"].get("end") and TL["anchor"].get("end_presenter"))
    if logo is not None and t < TL["vo_end"] - 0.25 and not anchor.inter(logo_box(logo), anchor.face_at(TL, t)):
        x, y, w = LOGO
        c.drawImageRect(logo, skia.Rect.MakeXYWH(x, y, w, w * logo.height() / logo.width()), skia.SamplingOptions(skia.FilterMode.kLinear),
                        skia.Paint(Alphaf=0.92 * prog(t, 0.3, 0.4)))
    if TL.get("kcaps"):
        kinetic.draw_kcaps(c, t, TL)
    else:
        compose.draw_captions(c, t, TL)
    if end:
        kinetic.draw_end_panel(c, t, TL, logo)
    elif logo is not None:
        compose.draw_end(c, t, TL, logo)
    F = anchor.face_at(TL, t)
    if F is None or F[1] > 8:                                 # the progress bar, unless her head reaches the top edge
        c.drawRect(skia.Rect(0, 0, W * t / TL["end"], 8), skia.Paint(Color4f=col(compose.AMBER, 0.9)))


def render(TL, f0, f1, out):
    load_fonts(TL["fonts_dir"]); compose._serif()
    compose.apply_palette(TL.get("palette") or {})
    fr = Frames(TL); grade = TL.get("grade")
    logo = skia.Image.open(TL["logo"]) if TL.get("logo") and Path(TL["logo"]).exists() else None
    grain = [np.random.default_rng(i).normal(0, 4.5, (H // 2, W // 2)).astype(np.float32) for i in range(6)]
    yy, xx = np.mgrid[0:H, 0:W]
    vig = (1 - 0.34 * (((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 1.6)) ** 2)).clip(0.5, 1)[..., None].astype(np.float32)
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                            *compose.encoder_args(), "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", out],
                           stdin=subprocess.PIPE)
    surf = skia.Surface(W, H)
    for fi in range(f0, f1):
        t = fi / FPS
        img, mask, s = fr.at(t, fi)
        if grade:
            img = proedit.apply_grade(img, grade)
        f = img.astype(np.float32) * vig * compose.brightness(t, TL) + cv2.resize(grain[fi % 6], (W, H))[..., None]
        rgba = cv2.cvtColor(np.clip(f, 0, 255).astype(np.uint8), cv2.COLOR_BGR2RGBA)
        c = surf.getCanvas(); c.clear(skia.ColorBLACK)
        c.drawImage(skia.Image.fromarray(rgba, colorType=skia.kRGBA_8888_ColorType), 0, 0)
        behind = [g for g in TL["graphics"] if g["type"] == "behind_text" and not g.get("hide") and g["t0"] - 0.05 <= t < g["t1"] + 0.6]
        if behind and not (s["kind"] == "anchor" and mask is None):          # words behind her: never over a face without a mask
            for g in behind:
                compose.DRAW["behind_text"](c, t, g)
            if mask is not None:                                              # she is drawn back on top of them
                person = rgba.copy(); person[..., 3] = cv2.GaussianBlur(mask, (5, 5), 0)
                c.drawImage(skia.Image.fromarray(person, colorType=skia.kRGBA_8888_ColorType, alphaType=skia.kUnpremul_AlphaType), 0, 0)
        draw_overlays(c, t, TL, s, logo)
        enc.stdin.write(surf.toarray(colorType=skia.kRGBA_8888_ColorType).tobytes())
    enc.stdin.close(); enc.wait()
    if enc.returncode != 0:
        raise RuntimeError(f"ffmpeg encode failed for frames {f0}-{f1}")

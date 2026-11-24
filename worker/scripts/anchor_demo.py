"""Anchor-mode look test without the Mac: a still portrait stands in for the HeyGen clip (a hand-made person mask stands in
for Apple Vision), two scenes with generated background plates, kinetic captions, kinetic words + strike, a scene
transition, the stamp and the side-panel end card. Everything goes through the real timeline, layout, compositor and mix.

    python worker/scripts/anchor_demo.py --portrait presenter.png --out /tmp/anchor_demo
writes demo.mp4 (about 6.5 s, sound effects only: there is no recorded voice here) and frame_*.png."""
import argparse, json, os, shutil, subprocess, sys
from pathlib import Path

WORKER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKER))
os.environ.setdefault("REELSTUDIO_FONTS_DIR", "/tmp/rsfonts")
import cv2, numpy as np, soundfile as sf, skia  # noqa: E402
from reelworker.media import timeline, anchor, render, mix, gfx_base  # noqa: E402

W, H = 1080, 1920


def presenter_still(src_path, out_dir):
    """Portrait (any size) -> a 1080x1920 presenter frame + a hand-made person mask (head, bun, neck, torso)."""
    src = cv2.imread(str(src_path)); h, w = src.shape[:2]
    m = np.zeros((h, w), np.uint8)
    sx, sy = w / 1213, h / 1297                                    # drawn for the 1213x1297 reference portrait
    P = lambda x, y: (int(x * sx), int(y * sy))  # noqa: E731
    cv2.ellipse(m, P(522, 262), (int(212 * sx), int(250 * sy)), 0, 0, 360, 255, -1)
    cv2.ellipse(m, P(340, 360), (int(55 * sx), int(75 * sy)), 0, 0, 360, 255, -1)
    cv2.rectangle(m, P(455, 420), P(640, 560), 255, -1)
    cv2.fillPoly(m, [np.array([P(*q) for q in ((330, 525), (720, 525), (880, 570), (970, 690), (1035, 940), (1045, 1297), (135, 1297),
                                                (140, 1010), (160, 720), (225, 600))], np.int32)], 255)
    gc = np.where(m > 0, cv2.GC_PR_FGD, cv2.GC_PR_BGD).astype(np.uint8)     # refine the hand-drawn shapes on the picture itself
    k = int(40 * sx); gc[cv2.erode(m, np.ones((k, k), np.uint8)) > 0] = cv2.GC_FGD
    gc[cv2.dilate(m, np.ones((k, k), np.uint8)) == 0] = cv2.GC_BGD
    gc[int(h * 0.82):][m[int(h * 0.82):] > 0] = cv2.GC_FGD                    # the desk edge stays with her
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.grabCut(src, gc, None, bgd, fgd, 5, cv2.GC_INIT_WITH_MASK)
    m = np.where((gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    s = 1.35 * 1213 / w                                            # medium shot: her face at about a third of the height
    big = cv2.resize(src, None, fx=s, fy=s, interpolation=cv2.INTER_LANCZOS4); mb = cv2.resize(m, None, fx=s, fy=s)
    cx = int(610 * sx * s); x0 = max(0, min(big.shape[1] - W, cx - W // 2)); y0 = H - big.shape[0]
    frame = np.zeros((H, W, 3), np.uint8); mask = np.zeros((H, W), np.uint8)
    crop = big[max(0, -y0):, x0:x0 + W]; frame[max(0, y0):, :crop.shape[1]] = crop; mask[max(0, y0):, :crop.shape[1]] = mb[max(0, -y0):, x0:x0 + W]
    if y0 > 0:                                                     # the room above her: her own wall, stretched and blurred
        frame[:y0] = cv2.GaussianBlur(cv2.resize(frame[y0:y0 + 40], (W, y0)), (0, 0), 25)
    cv2.imwrite(str(out_dir / "presenter.png"), frame); cv2.imwrite(str(out_dir / "presenter_mask.png"), cv2.GaussianBlur(mask, (0, 0), 2))
    return out_dir / "presenter.png", out_dir / "presenter_mask.png"


def plate_runway(path):
    """Golden-hour sky over a runway (generated, photo-like once the depth blur is on)."""
    Wp, Hp = 1296, 2304; yy, xx = np.mgrid[0:Hp, 0:Wp].astype(np.float32)
    hz = Hp * 0.66; t = np.clip(yy / hz, 0, 1)[..., None]
    sky = (1 - t) * np.array([120, 60, 22]) + t * np.array([90, 160, 255])            # BGR: deep blue -> warm orange
    sun = np.exp(-(((xx - Wp * 0.68) / 260) ** 2 + ((yy - hz * 0.93) / 200) ** 2))[..., None] * np.array([120, 210, 255])
    rng = np.random.default_rng(4); n = cv2.resize(rng.random((24, 14)).astype(np.float32), (Wp, Hp), interpolation=cv2.INTER_CUBIC)
    n2 = cv2.resize(rng.random((96, 54)).astype(np.float32), (Wp, Hp), interpolation=cv2.INTER_CUBIC)
    cloud = np.clip((0.6 * n + 0.4 * n2 - 0.55) * 3, 0, 1) * np.clip(1 - yy / hz, 0, 1) * 0.55
    img = sky * (1 - cloud[..., None]) + np.array([200, 215, 240]) * cloud[..., None] + sun
    g = yy >= hz; gt = np.clip((yy - hz) / (Hp - hz), 0, 1)
    ground = np.array([40, 52, 60]) * (1 - gt[..., None]) + np.array([24, 28, 32]) * gt[..., None]
    img = np.where(g[..., None], ground, img)
    for k in range(-3, 4):                                                               # runway edges + centre dashes in perspective
        x_far, x_near = Wp * 0.5 + k * 18, Wp * 0.5 + k * 420
        for y in range(int(hz), Hp, 6):
            u = (y - hz) / (Hp - hz); x = x_far + (x_near - x_far) * u
            if k in (-3, 3) and int(u * 40) % 2 == 0:
                cv2.circle(img, (int(x), y), max(1, int(2 + 6 * u)), (120, 200, 255), -1)
            if k == 0 and int(u * 18) % 2 == 0:
                cv2.circle(img, (int(x), y), max(1, int(1 + 5 * u)), (235, 240, 245), -1)
    cv2.imwrite(str(path), np.clip(img, 0, 255).astype(np.uint8))


def plate_studio(path):
    """Navy studio with warm and cool bokeh lights."""
    Wp, Hp = 1296, 2304; yy, xx = np.mgrid[0:Hp, 0:Wp].astype(np.float32)
    img = np.zeros((Hp, Wp, 3), np.float32) + np.array([58, 30, 12]) + (yy / Hp)[..., None] * np.array([-30, -16, -6])
    glow = np.exp(-(((xx - Wp * 0.5) / 700) ** 2 + ((yy - Hp * 0.35) / 900) ** 2))[..., None] * np.array([90, 50, 20])
    img += glow
    rng = np.random.default_rng(9); lay = np.zeros_like(img)
    for _ in range(46):
        x, y = rng.uniform(0, Wp), rng.uniform(0, Hp * 0.75); r = rng.uniform(25, 95)
        c = (60, 190, 255) if rng.random() < 0.5 else (255, 200, 120)
        cv2.circle(lay, (int(x), int(y)), int(r), [v * rng.uniform(0.25, 0.6) for v in c], -1, lineType=cv2.LINE_AA)
    img += cv2.GaussianBlur(lay, (0, 0), 6)
    cv2.imwrite(str(path), np.clip(img, 0, 255).astype(np.uint8))


def wordmark(path, fonts):
    """A plain text logo for the demo (the real reel uses the account's own logo file)."""
    gfx_base.load_fonts(fonts); s = skia.Surface(520, 200); c = s.getCanvas(); c.clear(skia.ColorTRANSPARENT)
    p = skia.Path(); p.moveTo(260, 18); p.lineTo(300, 78); p.lineTo(220, 78); p.close()
    c.drawPath(p, skia.Paint(Color4f=gfx_base.col((255, 196, 0)), AntiAlias=True))
    gfx_base.text(c, "WE ONE", 260, 140, gfx_base.font("pxb", 58), (255, 255, 255), shadow=False)
    gfx_base.text(c, "AVIATION", 260, 188, gfx_base.font("psb", 38), (255, 196, 0), shadow=False)
    cv2.imwrite(str(path), cv2.cvtColor(s.toarray(colorType=skia.kRGBA_8888_ColorType), cv2.COLOR_RGBA2BGRA))


def main():
    a = argparse.ArgumentParser(); a.add_argument("--portrait", required=True); a.add_argument("--out", required=True)
    a.add_argument("--fonts", default=os.environ["REELSTUDIO_FONTS_DIR"]); a.add_argument("--frames-only", action="store_true")
    o = a.parse_args(); out = Path(o.out); out.mkdir(parents=True, exist_ok=True); work = out / "_work"; work.mkdir(exist_ok=True)
    clip, matte = presenter_still(o.portrait, work)
    plate_runway(work / "bg_runway.png"); plate_studio(work / "bg_studio.png"); wordmark(work / "logo.png", o.fonts)
    # the script: 3 groups of Hinglish lines (phrase lengths as a real voice would take them)
    lines = [("g1", [("Class 12 ke baad kya?", 1.25), ("Doctor? Engineer?", 1.05)]),
             ("g2", [("Ya phir *Pilot* banna hai?", 1.2)]),
             ("g3", [("Toh lijiye", 0.55), ("*Free* career counselling", 1.2)])]
    spec = {"groups": [], "shots": [], "captions": "kinetic", "ai_label": False,
            "graphics": [{"type": "kinetic_words", "at_group": "g1", "at_phrase": 1, "until_group": "g3", "params": {"words": ["Doctor?", "Engineer?", "Pilot!"]}},
                         {"type": "strike", "at_group": "g2", "at_phrase": 0, "params": {"word": "Doctor?"}},
                         {"type": "stamp", "at_group": "g3", "at_phrase": 1, "params": {"text": "FREE CAREER COUNSELLING"}}]}
    vo = {}
    for gid, ph in lines:
        t, spans = 0.0, []
        for cap, d in ph:
            spans.append([t, t + d]); t += d + 0.08
        f = work / f"{gid}.wav"; sf.write(f, np.zeros(int(48000 * t)), 48000)
        vo[gid] = {"file": str(f), "trim": [0.0, t - 0.08], "spans": spans}
        spec["groups"].append({"id": gid, "gap_after": 0.25, "phrases": [{"say": c, "caption": c} for c, _ in ph]})
    TL0 = timeline.build(spec, vo, o.fonts, str(work / "logo.png"))
    g3 = TL0["groups"][2]["t0"]
    spec["anchor"] = {"clip": str(clip), "map": [{"vo_t0": 0.0, "vo_t1": TL0["vo_end"], "clip_t0": 0.0}]}
    spec["scenes"] = [{"from_t": 0.0, "to_t": g3 + 0.08, "bg": {"image": str(work / "bg_runway.png")}, "pos": "right", "scale": 0.9, "label": "Runway, golden hour"},
                      {"from_t": g3 + 0.08, "to_t": 99, "bg": {"image": str(work / "bg_studio.png")}, "pos": "center", "scale": 1.0}]
    TL = timeline.build(spec, vo, o.fonts, str(work / "logo.png"))
    TL["anchor"]["matte"] = str(matte)                      # stands in for Apple Vision's mask
    for sc in TL["anchor"]["scenes"]:
        sc["bg"]["file"] = sc["bg"]["image"]
    for s in TL["shots"]:
        s["matte"] = TL["anchor"]["matte"]
    anchor.layout(TL)
    TL.update(end_title="FREE CAREER COUNSELLING", end_sub="Eligibility + DGCA roadmap, no commitment", end_cta="Form bhariye")
    tlp = work / "timeline.json"; tlp.write_text(json.dumps(TL, ensure_ascii=False, indent=1))
    from reelworker.media import compose
    picks = {"frame_1_kinetic_words.png": TL["graphics"][0]["reveal"][-1] + 0.3, "frame_2_strike.png": TL["graphics"][1]["t0"] + 0.35,
             "frame_3_stamp.png": TL["graphics"][2]["t0"] + 0.6, "frame_4_end_card.png": TL["end"] - 0.4}
    for name, t in picks.items():
        fi = int(t * 60); tmp = work / "one.mp4"
        compose.render(TL, fi, fi + 1, str(tmp))
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp), "-frames:v", "1", str(out / name)], check=True)
    if o.frames_only:
        return
    video = render.render_video(tlp, work, TL["end"], 2)
    wav = mix.build(TL, str(work / "none.wav"), str(work / "mix.wav"))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", wav, "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(out / "demo.mp4")], check=True)
    shutil.copy(tlp, out / "timeline.json")
    print(json.dumps({"end": TL["end"], "transitions": TL["transitions"], "frames": list(picks)}, indent=1))


if __name__ == "__main__":
    main()

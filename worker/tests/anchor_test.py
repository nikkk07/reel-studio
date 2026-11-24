"""Anchor mode (all opt-in): one presenter clip upfront, scenes behind her, short cutaways that keep changing, face-safe
layout, kinetic captions and graphics, side-panel end card, no AI tag when ai_label is false, anchor_plan.py, and a spec
WITHOUT the new fields rendering byte-identically to before.
python tests/anchor_test.py   (no network; needs ffmpeg, skia and the reel fonts in REELSTUDIO_FONTS_DIR; the byte-identical
checks read the pre-change code from git, commit ANCHOR_BASE, default f087886, and are skipped without it)"""
import importlib.util, json, math, os, subprocess, sys, tempfile, uuid
from pathlib import Path
ROOT = Path(tempfile.mkdtemp())
os.environ.update(REELSTUDIO_ROOT=str(ROOT), REELSTUDIO_BRAIN="mock", REELSTUDIO_FONTS_DIR=os.environ.get("REELSTUDIO_FONTS_DIR", "/tmp/rsfonts"))
WORKER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKER)); sys.path.insert(0, str(WORKER / "scripts"))
import cv2, numpy as np, soundfile as sf, skia  # noqa: E402
from reelworker import schemas  # noqa: E402
from reelworker.media import anchor, anchor_render, kinetic, compose, timeline, mix, sfxlib, gfx_base, footage, matte  # noqa: E402
from reelworker.jobs import reel  # noqa: E402
from reelworker.runner import JobContext  # noqa: E402

R = []
def ok(c, n): R.append(bool(c)); print(("PASS " if c else "FAIL ") + n, flush=True)
def skip(n): print("SKIP " + n, flush=True)
def ff(*a): subprocess.run(["ffmpeg", "-v", "error", "-y", *a], check=True)
FONTS = os.environ["REELSTUDIO_FONTS_DIR"]
W, H = gfx_base.W, gfx_base.H


def span_vo(groups, caption=None):
    vo = {}; spec_g = []
    for gid, durs in groups:
        t = 0.0; spans = []
        for dd in durs:
            spans.append([t, t + dd]); t += dd + 0.1
        vo[gid] = {"file": "", "trim": [0, t], "spans": spans}
        spec_g.append({"id": gid, "phrases": [{"caption": (caption or "Pilot banna hai *{g}* toh suniye {i}").format(g=gid, i=i), "say": "x"} for i in range(len(durs))]})
    return spec_g, vo


G, VO = span_vo([("g1", [1.6, 1.8]), ("g2", [2.0, 2.2]), ("g3", [2.1, 2.0, 1.9]), ("g4", [2.2, 2.4]), ("g5", [1.5, 1.8])])
BASE = {"groups": G, "graphics": [], "shots": [{"id": "c1", "query": "cockpit", "at_group": "g2", "at_phrase": 0, "cutaway": True},
                                               {"id": "c2", "query": "runway", "at_group": "g3", "at_phrase": 1, "cutaway": True},
                                               {"id": "s9", "query": "classroom", "at_group": "g1", "at_phrase": 0}]}
TL0 = timeline.build(BASE, VO, FONTS, "")
PH = [(g["id"], k, a, b) for g in TL0["groups"] for k, (a, b) in enumerate(g["spans"])]
MAP = [{"vo_t0": 0.0, "vo_t1": PH[2][2] - 0.05, "clip_t0": 0.0}, {"vo_t0": PH[4][2] - 0.05, "vo_t1": PH[4][3] + 0.05, "clip_t0": 4.0},
       {"vo_t0": PH[6][2] - 0.05, "vo_t1": PH[-1][3] + 0.1, "clip_t0": 6.2}]
SPEC = {**BASE, "anchor": {"clip": "clip.mp4", "map": MAP}}


def covered(TL):
    s = TL["shots"]
    return abs(s[0]["t0"]) < 1e-6 and abs(s[-1]["t1"] - TL["end"]) < 1e-3 and all(abs(a["t1"] - b["t0"]) < 1e-3 for a, b in zip(s, s[1:]))


# ---------------------------------------------------------------- 1. map -> timeline windows
TL = timeline.build(SPEC, VO, FONTS, "")
an = [s for s in TL["shots"] if s["kind"] == "anchor"]; cu = [s for s in TL["shots"] if s["kind"] == "cut"]
ok(len(an) == 3 and an[0]["t0"] == 0 and abs(an[1]["t0"] - MAP[1]["vo_t0"]) < 1e-3 and abs(an[1]["t1"] - MAP[1]["vo_t1"]) < 1e-3
   and an[2]["t1"] == TL["end"] and [s["clip_t0"] for s in an] == [0.0, 4.0, 6.2] and TL["anchor"]["end_presenter"],
   "AN01 each map item is a presenter window at its voice-over time, the last one stays through the end card")
ok(covered(TL) and all(s["presenter"] for s in an) and not any(s.get("presenter") for s in cu), "AN02 presenter windows + cutaways cover the whole reel, no gap, no overlap")
w = anchor.windows({"map": [{"vo_t0": 3, "vo_t1": 5, "clip_t0": 1}, {"vo_t0": 1, "vo_t1": 3.5, "clip_t0": 0}, {"vo_t0": 6, "vo_t1": 6.01, "clip_t0": 0}, {"bad": 1}]})
ok([(x["t0"], x["t1"]) for x in w] == [(1.0, 3.5), (3.5, 5.0)], "AN03 the map is sorted, overlaps trimmed, empty and broken items dropped")
T2 = timeline.build({**BASE, "anchor": {"clip": "c", "map": [{"vo_t0": 0.0, "vo_t1": 3.0, "clip_t0": 0}, {"vo_t0": 3.2, "vo_t1": 8.0, "clip_t0": 3.1}]}}, VO, FONTS, "")
ok(T2["shots"][0]["t1"] == 3.2 and T2["shots"][1]["t0"] == 3.2 and T2["shots"][1]["kind"] == "anchor", "AN04 a gap under 0.3 s between windows is no cutaway: she continues")
ok(TL0.get("anchor") is None and "transitions" not in TL0 and "words" not in TL0, "AN05 a spec without anchor gets no anchor fields at all")

# ---------------------------------------------------------------- 2. cutaways: short, changing, never frozen
gap1 = (an[0]["t1"], an[1]["t0"]); gap2 = (an[1]["t1"], an[2]["t0"])
p1 = [s for s in cu if gap1[0] - 1e-6 <= s["t0"] < gap1[1]]; p2 = [s for s in cu if gap2[0] - 1e-6 <= s["t0"] < gap2[1]]
ok(all(s["t1"] - s["t0"] <= anchor.CUT_MAX + 1e-6 for s in cu) and len(p1) >= 3 and len(p2) >= 2, "AN06 no cutaway piece is longer than 2 s; a 4.9 s window becomes 3 pieces")
ok({s["src"] for s in p1} == {"c1"} and {s["src"] for s in p2} == {"c2"} and len({s["var"] for s in p1}) == len(p1),
   "AN07 each window shows the cutaway anchored in it, every piece a different clip (variant) of it")
ok(any("longer than 4 s" in x for x in TL["anchor"]["warnings"]) and TL["anchor"]["need"]["c1"] == {str(s["var"]): s["need"] for s in p1},
   "AN08 a window over 4 s is reported; each clip variant is prepped for its own piece only")
T3 = timeline.build({**BASE, "shots": [{"id": "c7", "query": "x", "at_group": "g5", "at_phrase": 1, "cutaway": True}], "anchor": {"clip": "c", "map": MAP}}, VO, FONTS, "")
ok({s["src"] for s in T3["shots"] if s["kind"] == "cut"} == {"c7"} and covered(T3), "AN09 a window with no cutaway of its own borrows a spare cutaway")
T4 = timeline.build({**BASE, "shots": [], "anchor": {"clip": "c", "map": MAP}}, VO, FONTS, "")
ok(all(s["kind"] == "anchor" for s in T4["shots"]) and any("presenter continues" in x for x in T4["anchor"]["warnings"]) and covered(T4),
   "AN10 with no stock clip at all the presenter simply continues (warning)")

files = {"c1": ["c1v0", "c1v1", "c1v2"], "c2": ["c2v0", "c2v1"], "BG1": ["b0", "b1"]}
durs = {"c1v0": 0.8, "c1v1": 3.0, "c1v2": 1.2, "c2v0": 1.0, "c2v1": 6.0, "b0": 1.4, "b1": 9.0}
SC = {**SPEC, "scenes": [{"from_t": 0, "to_t": 7.5, "bg": {"query": "apron"}, "pos": "left"}, {"from_t": 7.5, "to_t": 99, "bg": {"image": "x.png"}}]}
TB = timeline.build(SC, VO, FONTS, ""); logs = []
anchor.bind(TB, files, durs, lambda m, lvl="info": logs.append((lvl, m)))
cb = [s for s in TB["shots"] if s["kind"] == "cut"]
ok(all(s["t1"] - s["t0"] <= anchor.CUT_MAX + 1e-6 and s["t1"] - s["t0"] + s.get("off", 0) <= durs[s["file"]] for s in cb) and covered(TB),
   "AN11 after binding no piece is longer than 2 s and none needs more seconds than its clip has (short clips are re-cut, never held)")
ok(all(a["file"] != b["file"] for a, b in zip(cb, cb[1:]) if abs(a["t1"] - b["t0"]) < 1e-3) and len({s["file"] for s in cb}) >= 4,
   "AN12 two pieces in a row never show the same clip")
pl = TB["anchor"]["scenes"][0]["plates"]
ok(pl and all(p["t1"] - p["t0"] <= anchor.PLATE_MAX + 1e-6 and p["t1"] - p["t0"] <= durs[p["file"]] for p in pl) and len({p["file"] for p in pl}) == 2
   and abs(pl[0]["t0"] - 0) < 1e-6 and abs(pl[-1]["t1"] - 7.5) < 1e-3 and all(abs(a["t1"] - b["t0"]) < 1e-3 for a, b in zip(pl, pl[1:])),
   "AN13 the moving background behind her rotates clips every 2-3 s, each within its clip's length")
TB2 = timeline.build(SC, VO, FONTS, ""); anchor.bind(TB2, {}, {}, lambda *a: None)
ok(all(s["kind"] == "anchor" for s in TB2["shots"]) and covered(TB2) and not TB2["anchor"]["scenes"][0]["plates"], "AN14 no clip found at all: she continues, the scene keeps her own background")
src = ROOT / "short.mp4"; ff("-f", "lavfi", "-i", "testsrc2=size=320x568:rate=30:duration=1.2", "-pix_fmt", "yuv420p", str(src))
o1 = footage.prep_shot(src, ROOT / "p_noloop.mp4", 0.0, 2.3, 0.5, loop=False)
o2 = footage.prep_shot(src, ROOT / "p_loop.mp4", 0.0, 2.3, 0.5)
ok(reel._duration(o1) <= 1.25 and reel._duration(o2) >= 2.2, "AN15 anchor mode preps a clip without looping (shorter instead); the default still loops as before")

# ---------------------------------------------------------------- 3. scene background composite (synthetic frames, hand-made mask)
yy, xx = np.mgrid[0:H, 0:W]
head = ((xx - W / 2) / 150) ** 2 + ((yy - 620) / 190) ** 2 <= 1
body = ((xx - W / 2) / 380) ** 2 + ((yy - 1650) / 760) ** 2 <= 1
MASK = ((head | body) * 255).astype(np.uint8)
PERSON = np.full((H, W, 3), 128, np.uint8); PERSON[MASK > 0] = (60, 80, 200)
BG = np.zeros((H, W, 3), np.uint8); BG[..., 0] = (xx * 255 / W).astype(np.uint8); BG[..., 1] = 160; BG[..., 2] = (yy * 255 / H).astype(np.uint8)
plate = cv2.GaussianBlur(BG, (0, 0), 5)
out, a = anchor_render.composite(PERSON, MASK, plate)
ok(np.abs(out[1500, 540].astype(int) - PERSON[1500, 540]).max() <= 1 and np.abs(out[300, 60].astype(int) - plate[300, 60]).max() <= 1 and a.max() > 0.99,
   "AN16 composite: she is kept inside her mask, the new background shows outside")
pp, mm = anchor_render.place(PERSON, MASK, 0.8, anchor.POS_DX["left"] * W)
cx0 = xx[:880][MASK[:880] > 128].mean(); cx1 = xx[:1090][mm[:1090] > 128].mean(); top0 = yy[MASK > 128].min(); top1 = yy[mm > 128].min()
ok(abs((cx1 - cx0) - anchor.POS_DX["left"] * W) < 6 and top1 > top0 + 0.15 * (H - top0) and mm[H - 1, int(cx1)] > 200,
   "AN17 pos left / scale 0.8: she moves left, shrinks toward the bottom edge and still stands on it")
d = ROOT / "sc"; d.mkdir()
cv2.imwrite(str(d / "p.png"), PERSON); cv2.imwrite(str(d / "m.png"), MASK); cv2.imwrite(str(d / "bg.png"), BG)
SP = {**BASE, "shots": [], "anchor": {"clip": str(d / "p.png"), "map": [{"vo_t0": 0, "vo_t1": 30, "clip_t0": 0}]},
      "scenes": [{"from_t": 0, "to_t": 5, "bg": {"image": str(d / "bg.png")}, "pos": "center"}, {"from_t": 5, "to_t": 99, "bg": {"image": str(d / "bg.png")}, "pos": "right", "scale": 1.1}]}
TS = timeline.build(SP, VO, FONTS, ""); TS["anchor"]["matte"] = str(d / "m.png")
for sc in TS["anchor"]["scenes"]: sc["bg"]["file"] = sc["bg"]["image"]  # noqa: E701
anchor.layout(TS)
fr, mk, s_ = anchor_render.Frames(TS).at(2.0)
raw = BG.astype(np.float32); hf = lambda im: float(np.abs(cv2.Laplacian(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY), cv2.CV_32F)).mean())  # noqa: E731
ok(np.abs(fr[1500, 540].astype(int) - PERSON[1500, 540]).max() <= 40 and np.abs(fr[300, 60].astype(int) - 128).max() > 25 and mk is not None and mk[1500, 540] > 240,
   "AN18 a still background scene: her outfit kept, the old grey wall gone, her mask follows the picture")
ok(hf(fr[100:400, 20:300]) < hf(BG[100:400, 20:300]) * 0.9 + 0.05 or hf(BG[100:400, 20:300]) < 0.5, "AN19 the background is slightly depth-blurred")
fr2, mk2, _ = anchor_render.Frames(TS).at(7.0)
ok(xx[mk2 > 128].mean() > W / 2 + 0.15 * W, "AN20 the next scene puts her on the right")
TN = timeline.build(SP, VO, FONTS, ""); anchor.layout(TN)                     # no mask (Linux): her own background
frn, mkn, _ = anchor_render.Frames(TN).at(2.0)
ok(mkn is None and (np.abs(frn.astype(int) - PERSON.astype(int)).max(2) > 3).mean() < 0.01, "AN21 without a person mask she is shown exactly as shot (her own background)")

# ---------------------------------------------------------------- 4. face-safe layout
gfx_base.load_fonts(FONTS); compose._serif()
LOGO = d / "logo.png"; cv2.imwrite(str(LOGO), np.full((60, 120, 3), 230, np.uint8)); logo = skia.Image.open(str(LOGO))
GFX = [{"type": "kinetic_words", "at_group": "g1", "at_phrase": 0, "params": {"words": ["Doctor?", "Engineer?", "Graduation?"]}},
       {"type": "strike", "at_group": "g1", "at_phrase": 1, "params": {"word": "Doctor?"}},
       {"type": "pill_row", "at_group": "g2", "at_phrase": 1, "params": {"items": ["Class 12", "Pass-out", "Graduation"]}},
       {"type": "chip", "at_group": "g3", "at_phrase": 0, "params": {"text": "DGCA medical", "y": 470}},
       {"type": "ui_card", "at_group": "g3", "at_phrase": 1, "params": {"title": "Class 2 medical", "body": "First step", "y": 0.3}},
       {"type": "big_text", "at_group": "g3", "at_phrase": 2, "params": {"text": "18+"}},
       {"type": "stamp", "at_group": "g4", "at_phrase": 0, "params": {"text": "FREE CAREER COUNSELLING"}},
       {"type": "arrow_cta", "at_group": "g5", "at_phrase": 0, "params": {"text": "Form bhariye"}}]


def layout_case(pos, scale, kinetic_caps=True, faces=None):
    sp = {**BASE, "shots": [], "graphics": GFX, "anchor": {"clip": str(d / "p.png"), "map": [{"vo_t0": 0, "vo_t1": 30, "clip_t0": 0}]},
          "scenes": [{"from_t": 0, "to_t": 99, "bg": {"image": str(d / "bg.png")}, "pos": pos, "scale": scale, "label": "Classroom"}]}
    if kinetic_caps:
        sp["captions"] = "kinetic"
    T = timeline.build(sp, VO, FONTS, str(LOGO)); T["anchor"]["matte"] = str(d / "m.png")
    T.update(end_title="FREE CAREER COUNSELLING", end_sub="Eligibility + roadmap", end_cta="Form bhariye")
    return anchor.set_faces(T, faces)


bad_geo, bad_px, n_px = [], [], 0
surf = skia.Surface(W, H)
for pos, scale, kc, faces in (("center", 1.0, True, None), ("left", 1.0, True, None), ("right", 0.9, True, None), ("center", 1.25, True, None),
                              ("center", 1.0, False, None), ("right", 1.0, True, {"A1": (0.3, 0.12, 0.7, 0.4)})):
    T = layout_case(pos, scale, kc, faces)
    for c_ in T.get("kcaps") or []:
        if not c_.get("hide") and anchor.inter(anchor.caption_box(c_["y"]), anchor.face_union(T, c_["show0"], c_["show1"])):
            bad_geo.append((pos, "kcap", c_["y"]))
    for g in T["graphics"]:
        if g.get("box") and not g.get("hide") and anchor.inter(g["box"], anchor.face_union(T, g["t0"], g["t1"] - 0.01)):
            bad_geo.append((pos, g["type"], g["box"]))
    for t in np.arange(0.05, T["end"], 0.1):
        c = surf.getCanvas(); c.clear(skia.ColorTRANSPARENT)
        anchor_render.draw_overlays(c, float(t), T, anchor.seg_at(T, float(t)), logo)
        F = anchor.face_at(T, float(t))
        if F is None:
            continue
        x0, y0, x1, y1 = [int(round(v)) for v in (max(0, F[0]), max(0, F[1]), min(W, F[2]), min(H, F[3]))]
        al = surf.toarray(colorType=skia.kRGBA_8888_ColorType)[y0:y1, x0:x1, 3]; n_px += 1
        if al.size and al.max() > 2:
            bad_px.append((pos, scale, round(float(t), 2), int(al.max())))
ok(not bad_geo, f"AN22 no caption or graphic box intersects her face box (6 layouts: centre/left/right, bigger, classic captions, measured face) {bad_geo[:3]}")
ok(not bad_px and n_px > 300, f"AN23 pixel check over {n_px} frames: nothing is drawn inside her face box, end card included {bad_px[:4]}")
T = layout_case("left", 1.0)
kw = T["graphics"][0]; F = anchor.face_union(T, kw["t0"], kw["t1"])
ok(kw.get("region") == "right" and kw["box"][0] >= F[2], "AN24 she stands left: the kinetic words go to the free right side")
T = layout_case("center", 1.0)
ok(T["graphics"][6].get("region") == "below" and T["graphics"][6]["box"][1] > anchor.face_union(T, T["graphics"][6]["t0"], T["graphics"][6]["t1"])[3],
   "AN25 she stands centre: the stamp goes below her chin, above the captions")
cen = T["graphics"][5]; s_on = anchor.seg_at(T, cen["t0"] + 0.5)
c = surf.getCanvas(); c.clear(skia.ColorTRANSPARENT); compose.DRAW["big_text"](c, cen["t0"] + 0.5, cen); drew = surf.toarray()[..., 3].max()
c.clear(skia.ColorTRANSPARENT); anchor_render.draw_overlays(c, cen["t0"] + 0.5, {**T, "graphics": [cen], "kcaps": []}, s_on, None)
ok(drew > 0 and surf.toarray()[200:1200, :, 3].max() == 0, "AN26 a centre card (big_text) is left out while she is on screen")

# ---------------------------------------------------------------- 5. kinetic captions: word timing
sp = {"groups": [{"id": "g1", "phrases": [{"say": "x", "caption": "Pilot banna *DGCA* exam se hota hai"}, {"say": "x", "caption": "Class twelve ke baad"}]}],
      "shots": [], "graphics": [], "captions": "kinetic"}
vo1 = {"g1": {"file": "", "trim": [0.5, 6.0], "spans": [[0.5, 3.5], [3.6, 6.0]]}}
T = timeline.build(sp, vo1, FONTS, ""); wd = T["words"]
p0 = [x for x in wd if x["p"] == 0]; s0 = T["groups"][0]["spans"][0]
ok([x["w"] for x in p0] == ["Pilot", "banna", "DGCA", "exam", "se", "hota", "hai"] and [x["hl"] for x in p0] == [0, 0, 1, 0, 0, 0, 0]
   and abs(p0[0]["t0"] - s0[0]) < 2e-3 and abs(p0[-1]["t1"] - s0[1]) < 2e-3 and all(a["t1"] <= b["t0"] + 1e-3 and a["t0"] < a["t1"] for a, b in zip(wd, wd[1:])),
   "AN27 each caption word gets its time inside its phrase, in order, *keyword* marked")
ok((p0[1]["t1"] - p0[1]["t0"]) > (p0[4]["t1"] - p0[4]["t0"]) and "words" not in footage.__dict__, "AN28 longer words (more syllables) get more time")
vo2 = {"g1": {**vo1["g1"], "words": [[0.5, 2.6, "पायलट"], [2.6, 2.9, "बनना"], [2.9, 3.1, "डीजीसीए"], [3.1, 3.5, "है"]]}}
T2 = timeline.build(sp, vo2, FONTS, ""); q0 = [x for x in T2["words"] if x["p"] == 0]
ok(q0[2]["t0"] > p0[2]["t0"] + 0.2 and abs(q0[0]["t0"] - p0[0]["t0"]) < 1e-3 and abs(q0[-1]["t1"] - p0[-1]["t1"]) < 1e-3,
   "AN29 with the recogniser's word times the captions follow the real speech rate (a slow start pushes later words later)")
ch = T["kcaps"]
ok(all(2 <= len(c_["words"]) <= 4 for c_ in ch) and sum(len(c_["words"]) for c_ in ch) == len(wd)
   and all(c_["show1"] <= b["show0"] + 0.08 for c_, b in zip(ch, ch[1:])), "AN30 2-4 words on screen at a time, every word shown, one chunk after the other")
c = surf.getCanvas(); imgs = []
for t in (ch[0]["words"][0]["t0"] + 0.1, ch[0]["words"][1]["t0"] + 0.1):
    c.clear(skia.ColorTRANSPARENT); kinetic.draw_kcaps(c, t, T); imgs.append(surf.toarray(colorType=skia.kRGBA_8888_ColorType)[..., :3].astype(int))
yel = lambda im: int(((im[..., 0] > 230) & (im[..., 1] > 170) & (im[..., 1] < 215) & (im[..., 2] < 60)).sum())  # noqa: E731
ok(imgs[0].sum() < imgs[1].sum() and yel(imgs[0]) > 200, "AN31 words pop in one by one; the word being said is brand yellow")
ok(schemas.SPEC["properties"]["captions"]["enum"] == ["classic", "kinetic"] and all(k not in schemas.SPEC["required"] for k in ("anchor", "scenes", "captions", "ai_label")),
   "AN32 anchor / scenes / captions / ai_label are optional spec fields")

# ---------------------------------------------------------------- 6. the new graphics
T = layout_case("left", 1.0)
gs = {g["type"]: g for g in T["graphics"]}
res = {}
for ty in ("kinetic_words", "strike", "pill_row", "arrow_cta", "stamp"):
    g = gs[ty]; vals = []
    for t in (g["t0"] - 0.3, (g["t0"] + g["t1"]) / 2, g["t1"] + 0.3):
        c.clear(skia.ColorTRANSPARENT); compose.DRAW[ty](c, t, g); vals.append(int(surf.toarray()[..., 3].astype(int).sum()))
    res[ty] = vals
ok(all(v[0] == 0 and v[1] > 0 and v[2] == 0 for v in res.values()), f"AN33 kinetic_words, strike, pill_row, arrow_cta, stamp draw only inside their time {res}")
k_ = gs["kinetic_words"]; st = gs["strike"]
ok(st["index"] == 0 and st["kwg"]["box"] == k_["box"] and [0, st["t0"]] in k_["strikes"] and len(k_["reveal"]) == 3 and k_["reveal"] == sorted(k_["reveal"]),
   "AN34 the strike lands on DOCTOR? of the kinetic words; the words pop in one after another")
sp_ = {**BASE, "groups": [{"id": "g1", "phrases": [{"say": "x", "caption": "Doctor ya engineer?"}, {"say": "x", "caption": "Nahi, pilot!"}]}], "shots": [],
       "graphics": [{"type": "kinetic_words", "at_group": "g1", "at_phrase": 0, "until_group": "g1", "until_phrase": 1, "params": {"words": ["DOCTOR?", "ENGINEER?", "PILOT!"]}}]}
Tw = timeline.build(sp_, {"g1": {"file": "", "trim": [0, 4], "spans": [[0, 1.8], [1.9, 3.6]]}}, FONTS, "")
wt = {x["w"].lower().strip("?!,"): x["t0"] for x in Tw["words"]}
ok(abs(Tw["graphics"][0]["reveal"][0] - (wt["doctor"] - 0.04)) < 1e-3 and abs(Tw["graphics"][0]["reveal"][1] - (wt["engineer"] - 0.04)) < 1e-3,
   "AN35 a kinetic word pops exactly when the voice says it")
c.clear(skia.ColorTRANSPARENT); kinetic.g_pill_row(c, 5.0, {"type": "pill_row", "t0": 0, "t1": 9, "box": [600, 300, 1000, 1200], "reveal": [0, 0.3, 0.6], "params": {"items": ["Class 12", "Pass-out", "Graduation"]}})
al = surf.toarray()[..., 3]; rows = np.where(al.max(1) > 0)[0]
ok(rows.max() - rows.min() > 250 and al[:, :570].max() == 0, "AN36 pill_row stacks into a column in a narrow side box and stays inside it")
gst = gs["stamp"]; land = kinetic.stamp_land(gst)
ok(compose.shake(land + 0.05, T, 7) != (0.0, 0.0) and compose.shake(land - 0.05, T, 7) == (0.0, 0.0), "AN37 the stamp shakes the frame as it lands, not before")
TLm = layout_case("left", 1.0); evs = []
_put_orig = sfxlib.ui_pop
ok(set(kinetic.DRAW) <= set(compose.DRAW) and set(kinetic.DRAW) <= set(schemas.GRAPHIC_TYPES), "AN38 the new graphics are in the compositor and the schema")

# ---------------------------------------------------------------- 7. sound: whoosh on transitions, pops, impact
vdir = ROOT / "vo"; vdir.mkdir()
def with_voice(T):
    T = json.loads(json.dumps(T))
    for g in T["groups"]:
        n = int((g["trim"][1] - g["trim"][0]) * 48000); sf.write(vdir / f"{g['id']}.wav", np.zeros(max(n, 4800)), 48000); g["file"] = str(vdir / f"{g['id']}.wav")
    return T
def run_mix(T, name, mod=mix):
    sfxlib.rng = np.random.default_rng(3); mod.build(T, str(ROOT / "none.wav"), str(ROOT / name)); return sf.read(ROOT / name)[0][:, 0]
SR = sfxlib.SR
Tm = with_voice(TL); m_all = run_mix(Tm, "a.wav"); m_no = run_mix({**Tm, "transitions": []}, "b.wav")
e = lambda x, t, d=0.15: float(np.sqrt(np.mean(x[int((t - d) * SR):int((t + d) * SR)] ** 2)))  # noqa: E731
tr = TL["transitions"]
ok(tr and all(e(m_all - m_no, x["t"] - 0.1) > 1e-3 for x in tr) and len(tr) < len(TL["shots"]) - 1,
   "AN39 a whoosh on every whip / zoom transition (and none on the quick cuts between cutaway pieces)")
Tg = with_voice(layout_case("left", 1.0)); mg = run_mix(Tg, "g.wav"); mg0 = run_mix({**Tg, "graphics": []}, "g0.wav")
kwg = next(g for g in Tg["graphics"] if g["type"] == "kinetic_words"); stg = next(g for g in Tg["graphics"] if g["type"] == "stamp")
ok(all(e(mg - mg0, r + 0.05, 0.06) > 1e-3 for r in kwg["reveal"]) and e(mg - mg0, stg["t0"] + 0.3, 0.2) > 5e-3 and np.abs(mg).max() < 1.0,
   "AN40 a pop on every kinetic word as it lands and an impact on the stamp, no clipping")

# ---------------------------------------------------------------- 8. end card + AI tag
T = layout_case("center", 1.0); e_ = T["anchor"]["end"]
Fe = anchor.face_at(T, T["end"] - 0.1)
ok(e_["panel"] == "right" and Fe[2] <= e_["x0"] - 0.03 * W and anchor.face_at(T, T["vo_end"] - 0.5)[2] > Fe[2] + 100,
   "AN41 end card: she slides aside, the panel side stays clear of her face")
c.clear(skia.ColorTRANSPARENT); anchor_render.draw_overlays(c, T["end"] - 0.1, T, anchor.seg_at(T, T["end"] - 0.1), logo)
al = surf.toarray()[..., 3]
ok(al[600:1500, int(e_["x0"]) + 30:W - 60].mean() > 60 and al[200:, :int(Fe[2])].max() <= 2, "AN42 the logo + offer panel fills the free side, nothing on her side")
T = layout_case("right", 1.0)
ok(T["anchor"]["end"]["panel"] == "left", "AN43 she stands right: the panel comes in on the left")
c.clear(skia.ColorTRANSPARENT); anchor_render.draw_overlays(c, 3.0, {**T, "ai_label": "Made with AI"}, anchor.seg_at(T, 3.0), None); tag = surf.toarray()[195:250, 600:1020, 3].max()
c.clear(skia.ColorTRANSPARENT); anchor_render.draw_overlays(c, 3.0, T, anchor.seg_at(T, 3.0), None); notag = surf.toarray()[195:250, 600:1020, 3].max()
ok(tag > 100 and notag == 0, "AN44 the on-screen AI tag is drawn only when the timeline carries one")


# ---------------------------------------------------------------- 9. stage_footage + build_timeline in anchor mode (no Kaggle, no AI tag)
SEARCHES, ASKED = [], []
saved = {k: getattr(footage, k) for k in ("search", "contact_sheets", "download", "prep_shot", "credits")}
CLIPS = ROOT / "clips"; CLIPS.mkdir()
ff("-f", "lavfi", "-i", "testsrc2=size=270x480:rate=30:duration=1.0", "-pix_fmt", "yuv420p", str(CLIPS / "short.mp4"))
ff("-f", "lavfi", "-i", "testsrc2=size=270x480:rate=30:duration=4.0", "-pix_fmt", "yuv420p", str(CLIPS / "long.mp4"))
footage.search = lambda q, per_source=6: (SEARCHES.append(q), [{"src": "pexels", "id": f"{abs(hash(q)) % 999}{i}", "q": q, "author": "a", "page": "p", "licence": "l"} for i in range(5)])[1]
footage.contact_sheets = lambda shots, cands, work, font: None
footage.download = lambda c_, dst: (Path(dst).parent.mkdir(parents=True, exist_ok=True), Path(dst).write_text(c_["id"]), Path(dst))[2]
PREPS = []
def fake_prep(src, dst, a_, dur, fx, loop=True):
    PREPS.append((Path(dst).name, dur, loop)); i = int(Path(src).read_text()[-1])
    ff("-i", str(CLIPS / ("short.mp4" if i == 1 else "long.mp4")), "-t", f"{dur:.2f}", "-c", "copy", str(dst)); return Path(dst)
footage.prep_shot = fake_prep
footage.credits = lambda chosen: [{"shot": k, "author": v.get("author")} for k, v in chosen.items()]
class Brain:
    def prompt(self, name, **v): return json.dumps(v)
    def ask(self, name, text, schema, tools, cwd, timeout=0, on_event=None):
        ASKED.append(json.loads(text.split("---\n\n", 1)[-1])["shots"])
        return {"picks": [{"shot_id": l.split(":")[0][2:], "candidate": l.split(":")[0][2:] + "-1", "alternates": [l.split(":")[0][2:] + "-2"]}
                          for l in ASKED[-1].splitlines()]}
class DB:
    def update(self, *a, **k): return [{}]
    def insert(self, *a, **k): return [{}]
    def select(self, *a, **k): return []
def kaggle(*a, **k): raise AssertionError("anchor mode must never call Kaggle")
OUTS = {}
sv = (reel.brain, reel.agents.brain, reel._vo_map, reel._brand, reel.brandmod.logo, reel._presenter_shots, matte.available)
reel.brain = reel.agents.brain = Brain(); reel._vo_map = lambda ctx: VO; reel.brandmod.logo = lambda p, db=None: str(LOGO)
reel._brand = lambda ctx: {"name": "We One Aviation", "niche": "aviation", "visual_style": {"look": "premium"}}
reel._presenter_shots = kaggle; matte.available = lambda: True
HEY = ROOT / "heygen.mp4"; ff("-f", "lavfi", "-i", "testsrc2=size=270x480:rate=25:duration=12", "-pix_fmt", "yuv420p", str(HEY))
ASPEC = {**BASE, "title": "t", "anchor": {"clip": str(HEY), "map": MAP}, "captions": "kinetic", "ai_label": False,
         "scenes": [{"from_t": 0, "to_t": 4.0, "bg": {"query": "airport apron wide"}, "pos": "left"}, {"from_t": 4.0, "to_t": 99, "bg": {"image": str(d / "bg.png")}}]}
j = {"id": str(uuid.uuid4()), "kind": "reel", "brand": "weone", "input": {"video": "presenter_studio", "ad": True}, "output": {}, "parent_id": None}
cx = JobContext(DB(), j, "mac"); cx.trace = lambda *a, **k: None; cx.merge_output = lambda o=None, **k: OUTS.update(o or {})
LOGS = []; cx.log = lambda m, lvl="info", **k: LOGS.append((lvl, m))
cx.path("spec.json").write_text(json.dumps(ASPEC))
reel.stage_footage(cx, lambda f: None)
fj = json.loads(cx.path("footage.json").read_text())
need = timeline.build(ASPEC, VO, FONTS, "")["anchor"]["need"]
ok("cockpit" in SEARCHES and "runway" in SEARCHES and "airport apron wide" in SEARCHES and "classroom" not in SEARCHES
   and any("BG1" in a_ and "background plate" in a_ for a_ in ASKED), "AN45 anchor footage: no Kaggle; only the cutaways and the scene plates are searched and picked")
ok(all(len(fj["variants"][k]) == len(v) for k, v in need.items()) and all(not lp and du <= anchor.PLATE_MAX + 0.31 for _, du, lp in PREPS)
   and len(set(fj["variants"]["c1"])) == 3, "AN46 each source gets as many different clips as it has pieces, each prepped short and never looped")
ok(fj["anchor"]["clip"] == str(HEY) and fj["chosen"]["anchor"]["author"].startswith("AI presenter (HeyGen)") and all(fj["durations"][f] > 0.3 for f in fj["durations"]),
   "AN47 the presenter clip and every clip's real length are recorded; the presenter is credited as AI")
TLr, tlp = reel.build_timeline(cx)
cb = [s for s in TLr["shots"] if s["kind"] == "cut"]
ok(all(s["t1"] - s["t0"] <= anchor.CUT_MAX + 1e-6 and s["t1"] - s["t0"] + s.get("off", 0) <= fj["durations"][s["file"]] + 1e-6 for s in cb) and covered(TLr),
   "AN48 the built timeline: no cutaway piece over 2 s and none longer than its clip (the 1 s clip is cut away from in time)")
ok("ai_label" not in TLr and "platform" in OUTS.get("ai_disclosure", "") and TLr["end_cta"] == "Form bhariye" and TLr["end_title"] == "FREE CAREER COUNSELLING",
   "AN49 ai_label false: no AI tag on the video (the platform label is noted instead); the ad ends on its offer")
ok(TLr["anchor"]["matte"] is None and any("own background" in m for _, m in LOGS) and TLr["kcaps"], "AN50 Linux (no Vision): warning, her own background, the reel still builds")
for k_, v in saved.items():
    setattr(footage, k_, v)
reel.brain, reel.agents.brain, reel._vo_map, reel._brand, reel.brandmod.logo, reel._presenter_shots, matte.available = sv

# ---------------------------------------------------------------- 10. a real anchor render: presenter clip, cutaways, transitions
TLr["fonts_dir"] = FONTS; TLr["logo"] = str(LOGO)
tr0 = TLr["transitions"][0]["t"]; f0 = int((tr0 - 0.2) * 60)
compose.render(TLr, f0, f0 + 24, str(ROOT / "anchor.mp4"))
cap = cv2.VideoCapture(str(ROOT / "anchor.mp4")); n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)); frs = [cap.read()[1] for _ in range(n)]
diffs = [float(np.abs(a_.astype(int) - b_.astype(int)).mean()) for a_, b_ in zip(frs, frs[1:])]
ok(n == 24 and frs[0].shape == (H, W, 3) and max(diffs) > 5, "AN51 compositor renders an anchor timeline (presenter -> whip -> cutaway) at 1080x1920")
ok(TLr["anchor"].get("tail") and Path(TLr["anchor"]["tail"]).exists(), "AN52a the presenter clip's last 3 s are prepared reversed for the end card")
Tf = json.loads(json.dumps(TLr)); Tf["shots"] = [s for s in Tf["shots"] if s["kind"] == "anchor"][:1]; Tf["shots"][0].update(t0=0, t1=Tf["end"], clip_t0=0.0, vo_t0=0.0)
fx_ = anchor_render.Frames(Tf); S0 = Tf["shots"][0]; last = 12.0 - 1 / 25
a1 = fx_.presenter(S0, 11.95)[0]; a2 = fx_.presenter(S0, 12.6)[0]; a4 = fx_.presenter(S0, 13.04)[0]
fx2 = anchor_render.Frames({**Tf, "anchor": {**Tf["anchor"], "tail": None}}); a3 = fx2.presenter(S0, last - (12.6 - last))[0]; a5 = fx2.presenter(S0, last - (13.04 - last))[0]
ok(np.abs(a2.astype(int) - a1.astype(int)).mean() > 1 and np.abs(a2.astype(int) - a3.astype(int)).mean() < 2.5 and np.abs(a4.astype(int) - a5.astype(int)).mean() < 2.5,
   "AN52 past the end of the presenter clip it plays back (ping-pong), never a frozen frame")
GR = ROOT / "grad.mp4"
ff("-f", "lavfi", "-i", "color=c=black:s=270x480:r=10:d=12", "-vf", "geq=lum='N*2':cb=128:cr=128", "-pix_fmt", "yuv420p", str(GR))
Tc = json.loads(json.dumps(TLr)); Tc["anchor"]["clip"] = str(GR)
seg2 = [s for s in Tc["shots"] if s["kind"] == "anchor"][1]; tt = seg2["t0"] + 0.5
want = int((seg2["clip_t0"] + tt - seg2["vo_t0"]) * 10) * 2
got = float(cv2.cvtColor(anchor_render.Frames(Tc).presenter(seg2, tt)[0], cv2.COLOR_BGR2GRAY)[960, 540])
ok(abs(got - want) <= 8, f"AN53 a window shows the presenter clip from its clip_t0, same speed (clip frame {want // 2}, grey {got:.0f})")

# ---------------------------------------------------------------- 11. anchor_plan.py: on-camera audio + map
import anchor_plan  # noqa: E402
job = ROOT / "job"; (job / "vo").mkdir(parents=True)
ps = [("g1", [1.4, 1.6]), ("g2", [1.8]), ("g3", [1.2, 1.5])]; vout = {}; spec_j = {"title": "t", "groups": [], "graphics": [], "claims": [],
                                                                            "shots": [{"id": "s1", "query": "a", "at_group": "g1", "at_phrase": 0}, {"id": "s2", "query": "b", "at_group": "g2", "at_phrase": 0}]}
for k_, (gid, durs_) in enumerate(ps):
    t, spans, x = 0.05, [], []
    for i, du in enumerate(durs_):
        tt_ = np.arange(int(du * 48000)) / 48000; x.append(np.zeros(int(0.1 * 48000))) if i else x.append(np.zeros(int(0.05 * 48000)))
        x.append(0.3 * np.sin(2 * np.pi * (200 + 150 * k_ + 60 * i) * tt_ + 3 * np.sin(2 * np.pi * 1.3 * tt_))); spans.append([t, t + du]); t += du + 0.1
    a_ = np.concatenate(x + [np.zeros(4800)]); f = job / "vo" / f"{gid}.wav"; sf.write(f, a_, 48000)
    vout[gid] = {"file": str(f), "trim": [0.0, len(a_) / 48000], "spans": spans, "chosen": {}}
    spec_j["groups"].append({"id": gid, "gap_after": 0.4, "phrases": [{"say": "x", "caption": f"{gid} {i}"} for i in range(len(durs_))]})
(job / "voice_out.json").write_text(json.dumps(vout)); (job / "spec.json").write_text(json.dumps(spec_j))
res = anchor_plan.make(job, anchor_plan.parse_cuts(["g2:0"]), clip=str(job / "hey.mp4"))
TLp = timeline.build(spec_j, {g: {k: v[k] for k in ("file", "trim", "spans")} for g, v in vout.items()}, "", "")
g2 = TLp["groups"][1]
on_, sr_ = sf.read(res["oncam"]); mp = res["map"]
exp_len = sum(m["vo_t1"] - m["vo_t0"] for m in mp) - anchor_plan.XFADE * (len(mp) - 1)
ok(len(mp) == 2 and mp[0]["vo_t1"] <= g2["spans"][0][0] and mp[1]["vo_t0"] >= g2["spans"][0][1] and abs(len(on_) / sr_ - exp_len) < 2e-3
   and abs(mp[0]["vo_t1"] - (TLp["groups"][0]["spans"][-1][1] + 0.1)) < 1e-3, "AN54 anchor_plan: the cut phrase is left out; on-camera windows padded 0.1 s, joined with 20 ms crossfades")
trk = anchor_plan.track(TLp, TLp["vo_end"] + 0.5)
okc = True
for m in mp:
    for x in (0.1, 0.5, (m["vo_t1"] - m["vo_t0"]) - 0.1):
        a0 = int((m["vo_t0"] + x) * 48000); b0 = int((m["clip_t0"] + x) * 48000)
        okc &= np.abs(trk[a0:a0 + 2400] - on_[b0:b0 + 2400]).max() < 2e-3
ok(okc, "AN55 every map item points at exactly its own voice in oncam.wav (clip_t0 + x == vo_t0 + x)")
sa = json.loads(Path(res["spec"]).read_text())
Ta = timeline.build(sa, {g: {k: v[k] for k in ("file", "trim", "spans")} for g, v in vout.items()}, "", "")
ok(sa["anchor"]["map"] == mp and sa["anchor"]["clip"] == str(job / "hey.mp4") and res["marked_cutaway"] == ["s2"]
   and [s["src"] for s in Ta["shots"] if s["kind"] == "cut"] and all(s["src"] == "s2" for s in Ta["shots"] if s["kind"] == "cut") and covered(Ta),
   "AN56 the spec copy carries the anchor map, the cut phrase's shot is marked cutaway, and the timeline cuts to it there")
before = (job / "spec.json").read_text(); anchor_plan.make(job, anchor_plan.parse_cuts(["g2:0"]), clip=str(job / "hey.mp4"), apply=True)
ok(json.loads((job / "spec.json").read_text())["anchor"]["map"] == mp and (job / "spec.before_anchor.json").read_text() == before,
   "AN56a --apply makes it the job's spec (the old spec is kept) so the job resumes in anchor mode")
(job / "spec.json").write_text(before)
long_ = anchor_plan.make(job, anchor_plan.parse_cuts(["g1:1,g2:0,g3:0"]))
ok(any("lasts" in w_ for w_ in long_["warnings"]) and len(long_["map"]) == 2, "AN57 a cutaway window over 4 s is warned about")


# ---------------------------------------------------------------- 12. a spec WITHOUT the new fields: byte-identical to before
BASEC = os.environ.get("ANCHOR_BASE", "f087886")
def old(mod):
    r = subprocess.run(["git", "-C", str(WORKER), "show", f"{BASEC}:worker/reelworker/media/{mod}.py"], capture_output=True, text=True)
    if r.returncode:
        return None
    spec_ = importlib.util.spec_from_loader(f"reelworker.media._abase_{mod}", loader=None)
    m = importlib.util.module_from_spec(spec_); m.__package__ = "reelworker.media"; m.__file__ = str(WORKER / "reelworker" / "media" / f"{mod}.py")
    exec(compile(r.stdout, f"{BASEC}:{mod}.py", "exec"), m.__dict__)
    return m
o_tl, o_cmp, o_mix, o_ft = old("timeline"), old("compose"), old("mix"), old("footage")
if not (o_tl and o_cmp and o_mix and o_ft):
    skip(f"AN58-62 byte-identical checks: commit {BASEC} not available here")
else:
    PLAIN = {**BASE, "graphics": [{"type": t, "at_group": "g2", "at_phrase": 0, "params": {"text": "X", "to": 5, "items": [{"text": "a"}]}}
                                  for t in ("chip", "big_text", "counter", "flash", "list_card", "behind_text", "ui_card", "light_leak", "scene_label")],
             "grade": "warm_cinematic", "sfx": [{"type": "whoosh", "at_group": "g3"}, {"type": "ding", "at_group": "g4", "at_phrase": 1}]}
    ok(json.dumps(timeline.build(PLAIN, VO, FONTS, "l.png")) == json.dumps(o_tl.build(PLAIN, VO, FONTS, "l.png")),
       "AN58 timeline: identical to before for a spec without the new fields")
    TP = with_voice(timeline.build(PLAIN, VO, FONTS, "l.png"))
    ok(np.array_equal(run_mix(TP, "n.wav"), run_mix(TP, "o.wav", o_mix)), "AN59 mix: identical samples to before")
    rdir = ROOT / "rend"; rdir.mkdir()
    shot = rdir / "s1.mp4"; ff("-f", "lavfi", "-i", "testsrc2=size=1296x2304:rate=60:duration=1", "-pix_fmt", "yuv420p", str(shot))
    TLq = {"shots": [{"id": "s1", "file": str(shot), "t0": 0, "t1": 3, "z0": 1.0, "z1": 1.1}], "captions": [{"text": "hello *world*", "t0": 0.0, "t1": 0.4}],
           "graphics": [{"type": "chip", "t0": 0.0, "t1": 1, "params": {"text": "hi"}}, {"type": "flash", "t0": 0.1, "t1": 0.7, "params": {}},
                        {"type": "shake", "t0": 0.05, "t1": 0.6, "params": {}}],
           "vo_end": 2.0, "end": 3.0, "fonts_dir": FONTS, "logo": str(LOGO), "ai_label": "Video created using AI"}
    def frames(path):
        return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    compose.render(TLq, 0, 12, str(rdir / "new.mp4")); o_cmp.render(TLq, 0, 12, str(rdir / "old.mp4"))
    fn, fo = frames(rdir / "new.mp4"), frames(rdir / "old.mp4")
    ok(len(fn) == 12 * 1080 * 1920 * 3 and fn == fo, "AN60 compositor: byte-identical frames to before (captions, chip, flash, shake, AI tag)")
    footage.prep_shot(src, rdir / "pn.mp4", 0.0, 2.3, 0.5); o_ft.prep_shot(src, rdir / "po.mp4", 0.0, 2.3, 0.5)
    ok(frames(rdir / "pn.mp4") == frames(rdir / "po.mp4"), "AN61 footage prep: identical frames to before with the default arguments")
    r = subprocess.run(["git", "-C", str(WORKER), "show", f"{BASEC}:worker/reelworker/schemas.py"], capture_output=True, text=True)
    ns = {}; exec(r.stdout, ns)
    ok(ns["SPEC"]["required"] == schemas.SPEC["required"] and set(ns["GRAPHIC_TYPES"]) < set(schemas.GRAPHIC_TYPES)
       and all(schemas.SPEC["properties"][k] == v for k, v in ns["SPEC"]["properties"].items() if k not in ("shots", "graphics"))
       and schemas.SPEC["properties"]["shots"]["items"]["required"] == ns["SPEC"]["properties"]["shots"]["items"]["required"],
       "AN62 schema: nothing required changed, the old fields are untouched, the new ones are added")

print(f"\n{sum(R)}/{len(R)} passed")
sys.exit(0 if all(R) else 1)

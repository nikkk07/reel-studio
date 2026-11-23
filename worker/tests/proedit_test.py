"""Pro edit layer for ad reels (all opt-in): per-scene presenter locations, background swap behind the presenter, colour grade,
place sounds, light leak + scene label graphics, and a spec WITHOUT the new fields rendering byte-identically to before.
python tests/proedit_test.py   (no network, no Kaggle; needs ffmpeg, skia and the reel fonts in REELSTUDIO_FONTS_DIR;
the byte-identical checks read the pre-change code from git, commit PROEDIT_BASE, default 613c0db, and are skipped without it)"""
import importlib.util, json, os, subprocess, sys, tempfile, uuid
from pathlib import Path
ROOT = Path(tempfile.mkdtemp()); PACKD = ROOT / "library/presenter/pilot_f1"
os.environ.update(REELSTUDIO_ROOT=str(ROOT), REELSTUDIO_BRAIN="mock", REELSTUDIO_PRESENTER_DIR=str(PACKD),
                  REELSTUDIO_FONTS_DIR=os.environ.get("REELSTUDIO_FONTS_DIR", "/tmp/rsfonts"))
WORKER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKER))
import cv2, numpy as np, soundfile as sf, skia  # noqa: E402
from reelworker import schemas  # noqa: E402
from reelworker.media import presenter, proedit, compose, timeline, mix, sfxlib, gfx_base  # noqa: E402
from reelworker.jobs import reel  # noqa: E402
from reelworker.runner import JobContext  # noqa: E402

R = []
def ok(c, n): R.append(bool(c)); print(("PASS " if c else "FAIL ") + n, flush=True)
def skip(n): print("SKIP " + n, flush=True)
def ff(*a): subprocess.run(["ffmpeg", "-v", "error", "-y", *a], check=True)
FONTS = os.environ["REELSTUDIO_FONTS_DIR"]


# ---------------------------------------------------------------- 1. per-scene presenter locations
PACKD.mkdir(parents=True)
for n in ("studio_front.png", "studio_side.png", "apron.png", "classroom.png"):
    cv2.imwrite(str(PACKD / n), np.full((832, 480, 3), 120, np.uint8))
(PACKD / "pack.json").write_text(json.dumps({"name": "pilot_f1", "ai_generated": True, "prompt": "A young female airline pilot.",
                                             "shots": {"studio": ["studio_front.png", "studio_side.png"]}}))
d = presenter.pack(); logs = []
SC = {"g1": "apron.png", "g3": "classroom.png", "g4": "missing.png", "g5": "../../etc/apron.png"}
PL = {"apron.png": "Standing on an airport apron beside a parked trainer aircraft, late afternoon sun."}
img, place = presenter.scene_for(d, SC, PL, "g1", "studio", "hook", 0, logs.append)
ok(img == PACKD / "apron.png" and place.startswith("Standing on an airport apron"), "PE01 a group with a scene uses that picture and its place text")
img2, place2 = presenter.scene_for(d, SC, PL, "g3", "studio", "cta", 1, logs.append)
ok(img2 == PACKD / "classroom.png" and place2 is None, "PE02 a scene picture without place text keeps the style's own place")
img3, place3 = presenter.scene_for(d, SC, PL, "g2", "studio", "cta", 1, logs.append)
ok(img3 == presenter.image_for(d, "studio", "cta", 1) and place3 is None and not logs, "PE03 a group without a scene uses the normal rotation, silently")
img4, _ = presenter.scene_for(d, SC, PL, "g4", "studio", "hook", 0, logs.append)
ok(img4 == presenter.image_for(d, "studio", "hook", 0) and len(logs) == 1 and "missing.png" in logs[0], "PE04 a missing scene picture falls back to the rotation with a warning")
img5, place5 = presenter.scene_for(d, SC, PL, "g5", "studio", "hook", 0, logs.append)
ok(img5 == PACKD / "apron.png" and place5 == PL["apron.png"] and img5.parent == PACKD, "PE05 a scene path never leaves the character pack (only the file name is used)")
ok(presenter.scene_for(d, None, None, "g1", "studio", "hook", 0)[0] == presenter.image_for(d, "studio", "hook", 0)
   and presenter.scene_for(d, ["bad"], "bad", "g1", "studio", "hook", 0)[0] == presenter.image_for(d, "studio", "hook", 0),
   "PE06 no presenter_scenes (or a malformed one): exactly the old picture choice")
p_new = presenter.prompt_for(d, "studio", "hook", place); p_old = presenter.prompt_for(d, "studio", "hook")
ok("airport apron" in p_new and "studio" not in p_new.lower().replace("studio_", "") and "bright minimal studio" in p_old,
   "PE07 the place text replaces the style's place in the InfiniteTalk prompt; without it the prompt is unchanged")

# the whole make() path: each shot is sent with its scene's picture and prompt (Kaggle stand-ins)
sent = []
_orig = {k: getattr(presenter, k) for k in ("submit", "status", "collect", "prep", "cut", "spend")}
presenter.submit = lambda lane, item, audio, image, log: (sent.append((item, Path(image).name)), 0.0)[1]
presenter.status = lambda lane: "COMPLETE"
presenter.collect = lambda lane, name, out_dir: (out_dir.mkdir(parents=True, exist_ok=True), (out_dir / f"{name}.mp4").write_text("x"), out_dir / f"{name}.mp4")[2]
presenter.prep = lambda src, out, dur: (out.write_text("x"), out)[1]
presenter.cut = lambda track, t0, dur, out: (out.write_text("x"), out)[1]
presenter.spend = lambda h, now=None: {}
made = presenter.make([{"id": "P1", "role": "hook", "t0": 0, "need": 5, "track": ROOT / "t.wav", "group": "g1"},
                       {"id": "P2", "role": "cta", "t0": 20, "need": 5, "track": ROOT / "t.wav", "group": "g4"}],
                      "studio", ROOT / "pw", logs.append, lambda *a: None, lambda: None, scenes=SC, places=PL)
by = {it["name"]: (it, im) for it, im in sent}
ok(set(made) == {"P1", "P2"} and by["P1"][1] == "apron.png" and "airport apron" in by["P1"][0]["prompt"]
   and by["P2"][1] in ("studio_front.png", "studio_side.png") and "bright minimal studio" in by["P2"][0]["prompt"],
   "PE08 presenter.make sends the hook from the apron picture with the apron prompt; the missing scene falls back")
for k, v in _orig.items():
    setattr(presenter, k, v)


# ---------------------------------------------------------------- 2. background swap
def span_vo(groups):
    vo = {}; spec_g = []
    for gid, durs in groups:
        t = 0.0; spans = []
        for dd in durs:
            spans.append([t, t + dd]); t += dd + 0.1
        vo[gid] = {"file": "", "trim": [0, t], "spans": spans}
        spec_g.append({"id": gid, "phrases": [{"caption": f"*{gid}* line {i}", "say": "x"} for i in range(len(durs))]})
    return spec_g, vo
G, VO = span_vo([("g1", [1.6, 1.8, 1.5]), ("g2", [2.0, 2.2]), ("g3", [2.1, 2.0, 1.9]), ("g4", [2.2, 2.4, 2.6, 2.0])])
SPEC = {"groups": G, "graphics": [], "shots": [{"id": "s1", "query": "a", "at_group": "g1", "at_phrase": 0}, {"id": "s2", "query": "b", "at_group": "g2", "at_phrase": 0},
                                                {"id": "s3", "query": "c", "at_group": "g3", "at_phrase": 0}, {"id": "s4", "query": "d", "at_group": "g4", "at_phrase": 0}]}
A = reel.ai_spec(SPEC, VO, max_s=7.5)
A["bg_swap"] = [{"at_group": "g1", "query": "airport apron aircraft parked sunset", "note": "golden hour"}, {"at_group": "g2", "query": "x"},
                {"at_group": "g4"}, "junk"]
bgs = reel.bg_swap_shots(A, {"P1": "f", "P2": "f"})
ok(len(bgs) == 1 and bgs[0]["id"] == "P1_bg" and bgs[0]["bg_for"] == "P1" and bgs[0]["query"] == "airport apron aircraft parked sunset"
   and "golden hour" in bgs[0]["note"] and "no person close" in bgs[0]["note"],
   "PE09 one background plate per presenter shot in a bg_swap group (no query, no presenter, junk: ignored)")
ok(reel.bg_swap_shots(A, {"P2": "f"}) == [] and reel.bg_swap_shots(SPEC, {"P1": "f"}) == [], "PE10 no plate when the presenter shot was not made or the spec has no bg_swap")

# synthetic frames: a person-shaped ellipse on grey, a mask, a coloured moving background
Wf, Hf = 432, 768
yy, xx = np.mgrid[0:Hf, 0:Wf]
body = ((xx - Wf / 2) / (Wf * 0.28)) ** 2 + ((yy - Hf * 0.68) / (Hf * 0.34)) ** 2 <= 1
head = ((xx - Wf / 2) / (Wf * 0.13)) ** 2 + ((yy - Hf * 0.3) / (Hf * 0.09)) ** 2 <= 1
M = ((body | head) * 255).astype(np.uint8)
person = np.full((Hf, Wf, 3), 128, np.uint8); person[M > 0] = (60, 80, 200)                 # red-ish jacket on a grey studio wall
bgf = np.zeros((Hf, Wf, 3), np.uint8); bgf[..., 0] = (xx * 255 / Wf).astype(np.uint8); bgf[..., 1] = 150; bgf[..., 2] = (yy * 255 / Hf).astype(np.uint8)
a = proedit.feather(M)
row = a[int(Hf * 0.68)]; edge_x = int(np.argmax(M[int(Hf * 0.68)] > 0))
soft = int(((row > 0.05) & (row < 0.95)).sum())
ok(2 <= soft // 2 <= 12 and a[int(Hf * 0.68), edge_x] < 0.5 and a[int(Hf * 0.68), Wf // 2] > 0.99,
   f"PE11 the mask edge is pulled in and feathered ({soft // 2} px soft each side), the centre stays solid")
pl = proedit.plate(bgf, 0.5, (Hf, Wf))
out = proedit.composite(person, M, bgf, 0.5)
inside, outside = (Hf * 2 // 3, Wf // 2), (40, 30)
ok(np.abs(out[inside].astype(int) - person[inside].astype(int)).max() <= 1, "PE12 the presenter is preserved inside her mask")
ok(np.abs(out[outside].astype(int) - pl[outside].astype(int)).max() <= 1 and np.abs(out[outside].astype(int) - 128).max() > 20,
   "PE13 outside the mask the new background is visible (the old grey wall is gone)")
nowrap = proedit.composite(person, M, bgf, 0.5, wrap=0.0)
band = (a > 0.3) & (a < 0.98)
diff = np.abs(out.astype(int) - nowrap.astype(int)).sum(2)
ok(diff[band].mean() > 1.0 and diff[a > 0.999].max() <= 1 and diff[a < 0.001].max() == 0, "PE14 the light wrap only touches her edge")
ok(np.array_equal(proedit.plate(bgf, 0.0, (Hf, Wf), dof=0), bgf) and not np.array_equal(proedit.plate(bgf, 0.0, (Hf, Wf), dof=0), proedit.plate(bgf, 1.0, (Hf, Wf), dof=0)),
   "PE15 the plate starts untouched at 1.00 and drifts/pushes in over the shot (to 1.06)")
P = proedit.match_params([person], [M], [pl])
cm = proedit.color_match(person, P, 0.3)
lab_p, lab_c = (cv2.cvtColor(x, cv2.COLOR_BGR2LAB)[inside].astype(float) for x in (person, cm))
gap = P["mu_b"] - P["mu_p"]; moved = lab_c - lab_p
ok(np.all(np.abs(moved) <= np.abs(gap) * 0.45 + 2) and np.dot(moved, gap) > 0, "PE16 the colour match nudges her gently (~30 %) toward the plate's light")

clipdir = ROOT / "swap"; clipdir.mkdir()
def write_clip(path, frames, fps=30):
    vw = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{Wf}x{Hf}", "-r", str(fps), "-i", "-",
                           "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p", str(path)], stdin=subprocess.PIPE)
    for f_ in frames:
        vw.stdin.write(f_.tobytes())
    vw.stdin.close(); vw.wait()
write_clip(clipdir / "P1.mp4", [person] * 30)
write_clip(clipdir / "P1_matte.mp4", [cv2.cvtColor(M, cv2.COLOR_GRAY2BGR)] * 30)
write_clip(clipdir / "P1_bg.mp4", [np.roll(bgf, 4 * i, 1) for i in range(20)])                # shorter plate: holds its last frame
so = proedit.swap_shot(clipdir / "P1.mp4", clipdir / "P1_matte.mp4", clipdir / "P1_bg.mp4", clipdir / "P1_swap.mp4")
cap = cv2.VideoCapture(str(so)); n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)); okr, f0 = cap.read()
ok(n == 30 and f0.shape == person.shape and np.abs(f0[inside].astype(int) - person[inside].astype(int)).max() < 40
   and np.abs(f0[outside].astype(int) - 128).max() > 20, "PE17 swap_shot: same size and frame count, presenter kept, new background behind her")
m0 = so.stat().st_mtime_ns
ok(proedit.swap_shot(clipdir / "P1.mp4", clipdir / "P1_matte.mp4", clipdir / "P1_bg.mp4", clipdir / "P1_swap.mp4").stat().st_mtime_ns == m0,
   "PE18 a finished swap is reused (no second render)")
wl = []
s_nomask = {"id": "P1", "file": str(clipdir / "P1.mp4"), "matte": None}
ok(reel.swap_background(s_nomask, str(clipdir / "P1_bg.mp4"), lambda m, lvl="info": wl.append((lvl, m))) is False
   and s_nomask["file"].endswith("P1.mp4") and wl and wl[-1][0] == "warn" and "person mask" in wl[-1][1],
   "PE19 no person mask (Linux / no Apple Vision): the shot stays the normal presenter shot, with a warning")
(clipdir / "bad_matte.mp4").write_bytes(b"not a video")
s_bad = {"id": "P1", "file": str(clipdir / "P1.mp4"), "matte": str(clipdir / "bad_matte.mp4")}
ok(reel.swap_background(s_bad, str(clipdir / "P1_bg.mp4"), lambda m, lvl="info": wl.append((lvl, m))) is False and s_bad["file"].endswith("P1.mp4")
   and not s_bad.get("bg_swap"), "PE20 a broken mask or plate never crashes the render: warning, normal shot")
s_ok = {"id": "P1", "file": str(clipdir / "P1.mp4"), "matte": str(clipdir / "P1_matte.mp4")}
ok(reel.swap_background(s_ok, str(clipdir / "P1_bg.mp4"), lambda *a: None) and s_ok["file"].endswith("P1_swap.mp4") and s_ok["bg_swap"],
   "PE21 with a mask, the timeline shot plays the swapped clip (its mask still fits: she has not moved)")


# ---------------------------------------------------------------- 3. colour grade
lut = proedit.grade_lut("warm_cinematic")[0].astype(int)
ok(all(np.diff(proedit.curves(g), axis=0).min() > 0 and (np.diff(proedit.grade_lut(g)[0].astype(int), axis=0) >= 0).all()
       and (proedit.grade_lut(g)[0, 0] == 0).all() and (proedit.grade_lut(g)[0, 255] == 255).all() for g in proedit.GRADES),
   "PE22 every grade curve is monotonic, black stays black and white stays white")
ok(lut[200, 2] > lut[200, 0] and lut[50, 0] > lut[50, 2] and lut[60].mean() < 60 and lut[195].mean() > 195,
   "PE23 warm_cinematic: warm highlights, teal shadows, gentle S-curve")
fr = (np.random.default_rng(0).integers(0, 256, (64, 36, 3))).astype(np.uint8)
ok(proedit.apply_grade(fr, None) is fr and proedit.apply_grade(fr, "bogus") is fr and proedit.grade_lut(None) is None, "PE24 no grade: the frame is untouched")
gr = proedit.apply_grade(fr, "clean")
ok(gr.shape == fr.shape and gr.dtype == np.uint8 and np.abs(gr.astype(int) - fr.astype(int)).mean() < 12, "PE25 the clean grade is subtle")


# ---------------------------------------------------------------- 4. timeline + sound design
SP = {**SPEC, "graphics": [{"type": "light_leak", "at_group": "g2", "at_phrase": 0, "params": {"dur": 3}},
                           {"type": "scene_label", "at_group": "g1", "at_phrase": 1, "params": {"text": "Airport apron"}}],
      "grade": "warm_cinematic",
      "sfx": [{"type": "ambience_airport", "at_group": "g1", "at_phrase": 0, "until_group": "g2", "level": 0.4},
              {"type": "engine_riser", "at_group": "g2", "at_phrase": 0}, {"type": "whoosh", "at_group": "g3"},
              {"type": "impact", "at_group": "g3", "at_phrase": 1}, {"type": "ding", "at_group": "g4", "at_phrase": 2},
              {"type": "laser", "at_group": "g1"}, {"type": "ding", "at_group": "nope"}, "junk"]}
TL = timeline.build(SP, VO, FONTS, "")
gs = {g["t0"]: g for g in TL["groups"]}; g2 = TL["groups"][1]; g1 = TL["groups"][0]
ll = next(g for g in TL["graphics"] if g["type"] == "light_leak")
ok(abs(ll["t1"] - ll["t0"] - 1.0) < 1e-6 and TL["grade"] == "warm_cinematic", "PE26 timeline: light leak lasts 0.6-1.0 s; the grade is carried")
ev = {e["type"]: e for e in TL["sfx"]}
ok(len(TL["sfx"]) == 5 and abs(ev["engine_riser"]["t0"] - g2["spans"][0][0]) < 1e-3 and abs(ev["ambience_airport"]["t1"] - g2["t1"]) < 1e-3
   and ev["ambience_airport"]["level"] == 0.4, "PE27 timeline: each sound is keyed to its phrase; unknown types and groups are dropped")
TLn = timeline.build({**SPEC, "grade": "sepia", "sfx": []}, VO, FONTS, "")
ok("grade" not in TLn and "sfx" not in TLn, "PE28 an unknown grade or an empty sfx list adds nothing to the timeline")
evs = mix.pro_events(TL); SR = sfxlib.SR
riser = next(x for x in evs if x[2] is not None and len(x[2]) == int(2.0 * SR) and x[0] == "fx" and x[1] < ev["engine_riser"]["t0"])
amb = next(x for x in evs if x[0] == "amb")
ok(abs(riser[1] + len(riser[2]) / SR - ev["engine_riser"]["t0"]) < 1.5 / SR and np.abs(riser[2][-int(0.05 * SR):]).max() > 0.3 * np.abs(riser[2]).max(),
   "PE29 the engine riser ends exactly where its phrase starts, at full level")
ok(abs(len(amb[2]) / SR - (ev["ambience_airport"]["t1"] - ev["ambience_airport"]["t0"])) < 2 / SR and amb[1] == ev["ambience_airport"]["t0"],
   "PE30 the airport ambience spans its groups")
rms = lambda x: float(np.sqrt(np.mean(x ** 2)))  # noqa: E731
A_ = sfxlib.ambience_airport(6.0, 0.3)
ok(abs(rms(A_[int(SR):-int(SR)]) - 0.018) < 0.004 and abs(np.abs(sfxlib.engine_riser(1.5, 0.35)).max() - 0.35) < 1e-6
   and abs(np.abs(sfxlib.ding(0.25)).max() - 0.25) < 1e-6 and len(sfxlib.ding()) == int(1.6 * SR),
   "PE31 sound levels: ambience RMS 0.06 x level, riser and ding peaks = level")
ok(len(mix.pro_events({"sfx": [{"type": "engine_riser", "t0": 0.1}]})) == 0 and mix.pro_events({}) == [], "PE32 a riser with no room before the video starts is skipped")

# a real mix: the ambience is heard in the gaps and ducked under the voice
vdir = ROOT / "vo"; vdir.mkdir()
for g in TL["groups"]:
    t_ = np.arange(int((g["trim"][1] - g["trim"][0]) * 48000)) / 48000
    sf.write(vdir / f"{g['id']}.wav", 0.2 * np.sin(2 * np.pi * 180 * t_) * (np.sin(2 * np.pi * 3 * t_) > -0.2), 48000)
    g["file"] = str(vdir / f"{g['id']}.wav")
def run_mix(T, name, mod=mix):
    sfxlib.rng = np.random.default_rng(3)
    mod.build(T, str(ROOT / "no_roomtone.wav"), str(ROOT / name)); return sf.read(ROOT / name)[0][:, 0]
TLm = json.loads(json.dumps(TL)); TLm0 = {k: v for k, v in TLm.items() if k != "sfx"}
m1, m0 = run_mix(TLm, "with.wav"), run_mix(TLm0, "without.wav")
dlt = m1 - m0
gap = (int((g1["t1"] + 0.25) * SR), int((g2["t0"] - 0.02) * SR)); speech = (int((g1["t0"] + 1.5) * SR), int((g1["t0"] + 4.5) * SR))
ok(rms(dlt[gap[0]:gap[1]]) > 1e-4 and np.abs(m1).max() < 1.0 and len(m1) == len(m0),
   "PE33 the mix with place sounds renders at the same length without clipping")
amb_only = {**TLm0, "sfx": [x for x in TLm["sfx"] if x["type"] == "ambience_airport"]}
da = run_mix(amb_only, "amb.wav") - m0
ok(rms(da[gap[0]:gap[1]]) > 1.8 * rms(da[speech[0]:speech[1]]), "PE34 the airport bed breathes up in the gaps and ducks under the voice")


# ---------------------------------------------------------------- 5. graphics
gfx_base.load_fonts(FONTS); compose._serif()
surf = skia.Surface(gfx_base.W, gfx_base.H); c = surf.getCanvas()
def draw(gr_, t):
    c.clear(skia.Color(40, 40, 40)); compose.DRAW[gr_["type"]](c, t, gr_); return surf.toarray(colorType=skia.kRGBA_8888_ColorType)[..., :3].astype(int)
base = np.full((gfx_base.H, gfx_base.W, 3), 40)
LL = {"type": "light_leak", "t0": 1.0, "t1": 1.8, "params": {}}
mid, before, after = draw(LL, 1.4), draw(LL, 0.9), draw(LL, 2.2)
ok(mid.mean() > base.mean() + 8 and (mid >= base - 1).all() and np.array_equal(before, base) and np.array_equal(after, base)
   and mid[..., 0].mean() > mid[..., 2].mean(), "PE35 light_leak: an additive warm sweep, only inside its 0.8 s")
ok(draw({**LL, "params": {"from": "right", "color": "cyan", "strength": 2}}, 1.2).mean() > base.mean(), "PE36 light_leak options (side, colour, strength) draw without error")
SL = {"type": "scene_label", "t0": 0.0, "t1": 6.0, "params": {"text": "Airport apron", "sub": "Safdarjung, New Delhi"}}
lab_ = draw(SL, 1.0); y_ = int(gfx_base.H * 0.62)
ok(np.abs(lab_ - base).sum() > 0 and np.abs(lab_[y_ - 70:y_ + 60, :600] - base[y_ - 70:y_ + 60, :600]).sum() == np.abs(lab_ - base).sum()
   and np.array_equal(draw(SL, 3.5), base) and np.array_equal(draw({**SL, "params": {}}, 1.0), base),
   "PE37 scene_label: a small tag at the lower left that leaves after its hold (and nothing without text)")
ok(draw({**SL, "params": {"text": "Classroom", "side": "right", "y": 0.3, "color": "cyan"}}, 0.2).sum() > 0
   and {"light_leak", "scene_label"} <= set(schemas.GRAPHIC_TYPES) and {"light_leak", "scene_label"} <= set(compose.DRAW),
   "PE38 scene_label options draw; both types are in the schema and the compositor")
ok(set(schemas.SPEC["properties"]) >= {"presenter_scenes", "presenter_places", "bg_swap", "grade", "sfx"} and
   all(k not in schemas.SPEC["required"] for k in ("presenter_scenes", "presenter_places", "bg_swap", "grade", "sfx")),
   "PE39 the new spec fields are optional in the schema")


# ---------------------------------------------------------------- 6. a spec WITHOUT the new fields: byte-identical to before
BASE = os.environ.get("PROEDIT_BASE", "613c0db")
def old(mod):
    r = subprocess.run(["git", "-C", str(WORKER), "show", f"{BASE}:worker/reelworker/media/{mod}.py"], capture_output=True, text=True)
    if r.returncode:
        return None
    spec_ = importlib.util.spec_from_loader(f"reelworker.media._base_{mod}", loader=None)
    m = importlib.util.module_from_spec(spec_); m.__package__ = "reelworker.media"; m.__file__ = str(WORKER / "reelworker" / "media" / f"{mod}.py")
    exec(compile(r.stdout, f"{BASE}:{mod}.py", "exec"), m.__dict__)
    return m
o_tl, o_cmp, o_mix = old("timeline"), old("compose"), old("mix")
if not (o_tl and o_cmp and o_mix):
    skip(f"PE40-42 byte-identical checks: commit {BASE} not available here")
else:
    PLAIN = {**SPEC, "graphics": [{"type": t, "at_group": "g2", "at_phrase": 0, "params": {"text": "X", "to": 5, "items": [{"text": "a"}]}}
                                  for t in ("chip", "big_text", "counter", "flash", "list_card", "behind_text", "ui_card")]}
    ok(json.dumps(timeline.build(PLAIN, VO, FONTS, "l.png")) == json.dumps(o_tl.build(PLAIN, VO, FONTS, "l.png")),
       "PE40 timeline: identical to before for a spec without the new fields")
    ok(np.array_equal(run_mix(TLm0, "new.wav"), run_mix(TLm0, "old.wav", o_mix)), "PE41 mix: identical samples to before")
    rdir = ROOT / "rend"; rdir.mkdir()
    logo = rdir / "logo.png"; cv2.imwrite(str(logo), np.full((60, 120, 3), 200, np.uint8))
    shot = rdir / "s1.mp4"; ff("-f", "lavfi", "-i", "testsrc2=size=1296x2304:rate=60:duration=1", "-pix_fmt", "yuv420p", str(shot))
    TLr = {"shots": [{"id": "s1", "file": str(shot), "t0": 0, "t1": 3, "z0": 1.0, "z1": 1.1}], "captions": [{"text": "hello *world*", "t0": 0.0, "t1": 0.4}],
           "graphics": [{"type": "chip", "t0": 0.0, "t1": 1, "params": {"text": "hi"}}, {"type": "flash", "t0": 0.1, "t1": 0.7, "params": {}}],
           "vo_end": 2.0, "end": 3.0, "fonts_dir": FONTS, "logo": str(logo)}
    def frames(path):
        return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    compose.render(TLr, 0, 12, str(rdir / "new.mp4")); o_cmp.render(TLr, 0, 12, str(rdir / "old.mp4"))
    fn, fo = frames(rdir / "new.mp4"), frames(rdir / "old.mp4")
    ok(len(fn) == 12 * 1080 * 1920 * 3 and fn == fo, "PE42 compositor: byte-identical frames to before for a timeline without the new fields")
    compose.render({**TLr, "grade": "warm_cinematic"}, 0, 12, str(rdir / "graded.mp4"))
    fg = np.frombuffer(frames(rdir / "graded.mp4"), np.uint8).reshape(-1, 1920, 1080, 3); fo_ = np.frombuffer(fo, np.uint8).reshape(-1, 1920, 1080, 3)
    ok(fg.shape == fo_.shape and not np.array_equal(fg, fo_), "PE43 with grade set, the same frames come out graded")


# ---------------------------------------------------------------- 7. stage_footage: plates go through the normal search + Footage Picker
from reelworker.media import footage  # noqa: E402
SEARCHES, ASKED = [], []
saved = {k: getattr(footage, k) for k in ("search", "contact_sheets", "download", "prep_shot", "credits")}
footage.search = lambda q, per_source=6: (SEARCHES.append(q), [{"src": "pexels", "id": f"{abs(hash(q)) % 999}{i}", "q": q, "author": "a", "page": "p", "licence": "l"} for i in range(3)])[1]
footage.contact_sheets = lambda shots, cands, work, font: None
footage.download = lambda c_, dst: (Path(dst).parent.mkdir(parents=True, exist_ok=True), Path(dst).write_text(c_["q"]), Path(dst))[2]
footage.prep_shot = lambda src, dst, a_, b_, c_: (Path(dst).write_text(Path(src).read_text()), Path(dst))[1]
footage.credits = lambda chosen: [{"shot": k} for k in chosen]
class Brain:
    def prompt(self, name, **v): return json.dumps(v)
    def ask(self, name, text, schema, tools, cwd, timeout=0, on_event=None):
        ASKED.append(json.loads(text.split("---\n\n", 1)[-1])["shots"])
        return {"picks": [{"shot_id": l.split(":")[0][2:], "candidate": l.split(":")[0][2:] + "-1"} for l in ASKED[-1].splitlines()]}
class DB:
    def update(self, *a, **k): return [{}]
    def insert(self, *a, **k): return [{}]
    def select(self, *a, **k): return []
sv = (reel.brain, reel.agents.brain, reel._vo_map, reel._brand, reel.brandmod.logo, reel._presenter_shots)
reel.brain = reel.agents.brain = Brain(); reel._vo_map = lambda ctx: VO; reel.brandmod.logo = lambda p, db=None: "logo.png"
reel._brand = lambda ctx: {"name": "We One Aviation", "niche": "aviation", "visual_style": {"look": "premium", "footage_rules": ["no airline logos"]}}
reel._presenter_shots = lambda ctx, spec, vo, style: ({"P1": str(ROOT / "P1.mp4"), "P2": str(ROOT / "P2.mp4")}, A)
j = {"id": str(uuid.uuid4()), "kind": "reel", "brand": "weone", "input": {"video": "presenter_studio"}, "output": {}, "parent_id": None}
cx = JobContext(DB(), j, "mac"); cx.trace = lambda *a, **k: None; cx.merge_output = lambda *a, **k: None
cx.path("spec.json").write_text(json.dumps(SPEC))
reel.stage_footage(cx, lambda f: None)
fj = json.loads(cx.path("footage.json").read_text())
ok("airport apron aircraft parked sunset" in SEARCHES and "P1_bg" in ASKED[0] and "background plate" in ASKED[0]
   and fj["bg"] == {"P1": "P1_bg"} and fj["chosen"]["P1_bg"]["label"] == "P1_bg-1" and Path(fj["files"]["P1_bg"]).exists()
   and fj["chosen"]["P1"]["src"] == "ai", "PE44 the background plate is searched, shown to the Footage Picker and prepped like any clip, and credited")
j2 = {**j, "id": str(uuid.uuid4())}; cx2 = JobContext(DB(), j2, "mac"); cx2.trace = lambda *a, **k: None; cx2.merge_output = lambda *a, **k: None
cx2.path("spec.json").write_text(json.dumps(SPEC)); A2 = {k: v for k, v in A.items() if k != "bg_swap"}
reel._presenter_shots = lambda ctx, spec, vo, style: ({"P1": str(ROOT / "P1.mp4"), "P2": str(ROOT / "P2.mp4")}, A2)
n_s = len(SEARCHES); reel.stage_footage(cx2, lambda f: None)
fj2 = json.loads(cx2.path("footage.json").read_text())
ok("bg" not in fj2 and not any("_bg" in k for k in fj2["files"]) and len(SEARCHES) - n_s == len([s for s in A2["shots"] if not s.get("presenter")]),
   "PE45 without bg_swap, stage_footage searches and writes exactly what it did before")
reel.brain, reel.agents.brain, reel._vo_map, reel._brand, reel.brandmod.logo, reel._presenter_shots = sv
for k, v in saved.items():
    setattr(footage, k, v)

print(f"\n{sum(R)}/{len(R)} passed")
sys.exit(0 if all(R) else 1)

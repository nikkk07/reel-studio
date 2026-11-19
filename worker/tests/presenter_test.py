"""AI presenter on Kaggle (InfiniteTalk) + premium graphics.   python tests/presenter_test.py
A fake `kaggle` command stands in for Kaggle (records every call, 'renders' a clip with ffmpeg), so nothing leaves this machine."""
import json, os, stat, subprocess, sys, tempfile, uuid
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(tempfile.mkdtemp()); K = ROOT / "kaggle"; BIN = ROOT / "bin"; PACKD = ROOT / "library/presenter/pilot_f1"
os.environ.update(REELSTUDIO_ROOT=str(ROOT), REELSTUDIO_BRAIN="mock", REELSTUDIO_KAGGLE_DIR=str(K), REELSTUDIO_KAGGLE_BIN=str(BIN / "kaggle"),
                  REELSTUDIO_PRESENTER_DIR=str(PACKD), REELSTUDIO_KAGGLE_SETTLE_SECONDS="0", REELSTUDIO_KAGGLE_POLL_SECONDS="1",
                  REELSTUDIO_FONTS_DIR=os.environ.get("REELSTUDIO_FONTS_DIR", "/tmp/rsfonts"), FAKE_KAGGLE_LOG=str(ROOT / "kaggle.log"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reelworker import config, crm, brain  # noqa: E402
from reelworker.media import presenter, matte, compose  # noqa: E402
from reelworker.jobs import reel  # noqa: E402
from reelworker.runner import JobContext  # noqa: E402
import cv2, numpy as np  # noqa: E402

R = []
def ok(c, n): R.append(bool(c)); print(("PASS " if c else "FAIL ") + n, flush=True)
def ff(*a): subprocess.run(["ffmpeg", "-v", "error", "-y", *a], check=True)

# ---------------------------------------------------------------- fixtures: fake kaggle, kernels, character
BIN.mkdir(parents=True)
(BIN / "kaggle").write_text(r'''#!/usr/bin/env python3
import json, os, subprocess, sys, pathlib
log = os.environ["FAKE_KAGGLE_LOG"]; a = sys.argv[1:]
open(log, "a").write(json.dumps(a) + "\n")
st = pathlib.Path(log).with_suffix(".state"); s = json.loads(st.read_text()) if st.exists() else {}
mode = os.environ.get("FAKE_KAGGLE_MODE", "ok")
if a[:2] == ["kernels", "push"]:
    k = json.load(open(pathlib.Path(a[3]) / "kernel-metadata.json"))["id"]; s[k] = 0
    ds = pathlib.Path(a[3]).parent / "ds"; s[k + ":job"] = json.load(open(ds / "job.json"))
elif a[:2] == ["kernels", "status"]:
    s[a[2]] = s.get(a[2], 0) + 1
    print(f'{a[2]} has status "KernelWorkerStatus.' + ("ERROR" if mode == "error" else "RUNNING" if s[a[2]] < 2 else "COMPLETE") + '"')
elif a[:2] == ["kernels", "output"]:
    for it in s[a[2] + ":job"]["items"]:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=480x832:rate=25:duration=3", "-pix_fmt", "yuv420p",
                        str(pathlib.Path(a[4]) / (it["name"] + ".mp4"))], check=True)
st.write_text(json.dumps(s))
''')
(BIN / "kaggle").chmod(0o755)
for d, slug in (("it", "autoreel-infinitetalk"), ("itb", "autoreel-infinitetalk-b")):
    (K / d / "k").mkdir(parents=True); (K / d / "ds").mkdir(parents=True)
    (K / d / "k" / "kernel.py").write_text("# InfiniteTalk kernel made in testing (kept on the SSD)")
    (K / d / "k" / "kernel-metadata.json").write_text(json.dumps({"id": f"weoneaviation/{slug}"}))
    (K / d / "ds" / "patch_infinitetalk.py").write_text("# patch"); (K / d / "ds" / "old.wav").write_text("x")
PACKD.mkdir(parents=True)
for n in ("studio_front.png", "studio_side.png", "outdoor_walk.png"):
    cv2.imwrite(str(PACKD / n), np.full((832, 480, 3), 120, np.uint8))

# ---------------------------------------------------------------- character pack and budget
ok(presenter.pack() is None, "PR01 no pack.json and no anchor: no presenter (never guesses a character)")
(PACKD / "pack.json").write_text(json.dumps({"name": "pilot_f1", "ai_generated": False, "prompt": "x", "shots": {"studio": ["studio_front.png"]}}))
ok(presenter.pack() is None, "PR02 a character not marked as AI-generated is refused")
(PACKD / "pack.json").write_text(json.dumps({"name": "pilot_f1", "ai_generated": True, "prompt": "A young female airline pilot, white uniform shirt.",
                                             "shots": {"studio": ["studio_front.png", "studio_side.png", "missing.png"], "fast": ["outdoor_walk.png"]}}))
d = presenter.pack()
ok(d and d["shots"]["studio"] == ["studio_front.png", "studio_side.png"] and presenter.image_for(d, "fast", "hook").name == "outdoor_walk.png"
   and presenter.image_for(d, "studio", "hook").name != presenter.image_for(d, "studio", "cta").name,
   "PR03 pack: missing pictures dropped, each style uses its own pictures, hook and CTA get different shots")
mon = datetime(2026, 11, 16, 10, tzinfo=timezone.utc); sun = datetime(2026, 11, 22, 1, tzinfo=timezone.utc)
b0 = presenter.budget(mon)["left_h"]; presenter.spend(5, mon); b1 = presenter.budget(mon)["left_h"]
ok(b0 == presenter.WEEKLY_H and b1 == presenter.WEEKLY_H - 5 and presenter.budget(sun)["left_h"] == presenter.WEEKLY_H,
   "PR04 the weekly Kaggle budget counts hours spent and resets on Sunday (UTC)")
presenter.spend(-100, mon); (config.APP / "state" / "kaggle_budget.json").unlink()
ok(presenter.available(2)[0], "PR05 with a character, both kernels and budget, the presenter is available")
presenter.spend(presenter.WEEKLY_H - 1)
okb, why = presenter.available(2)
ok(not okb and "GPU time left" in why, "PR06 when the week's Kaggle hours run low, reels fall back to classic")
(config.APP / "state" / "kaggle_budget.json").unlink()

# ---------------------------------------------------------------- the shot plan
def span_vo(groups):            # groups: list of (id, [phrase durations])
    vo = {}; spec_g = []
    for gid, durs in groups:
        t = 0.0; spans = []
        for dd in durs:
            spans.append([t, t + dd]); t += dd + 0.1
        vo[gid] = {"file": "", "trim": [0, t], "spans": spans}
        spec_g.append({"id": gid, "phrases": [{"caption": "x", "say": "x"} for _ in durs]})
    return spec_g, vo
G, VO = span_vo([("g1", [1.6, 1.8, 1.5]), ("g2", [2.0, 2.2]), ("g3", [2.1, 2.0, 1.9]), ("g4", [2.2, 2.4, 2.6, 2.0])])
SPEC = {"groups": G, "graphics": [], "shots": [{"id": "s1", "query": "a", "at_group": "g1", "at_phrase": 0}, {"id": "s2", "query": "b", "at_group": "g2", "at_phrase": 0},
                                                {"id": "s3", "query": "c", "at_group": "g3", "at_phrase": 0}, {"id": "s4", "query": "d", "at_group": "g4", "at_phrase": 0}]}
A = reel.ai_spec(SPEC, VO, max_s=7.5)
VO_G2 = reel.timeline.build(SPEC, VO, "", "")["groups"][1]["t0"]
TLa = reel.timeline.build(A, VO, "", "")
sh = {s["id"]: s for s in TLa["shots"]}
ok(A["shots"][0]["id"] == "P1" and sh["P1"]["t0"] == 0 and sh["P1"]["t1"] <= 7.5 and A["shots"][-1]["id"] == "P2"
   and TLa["vo_end"] - sh["P2"]["t0"] <= 7.5 and A["shots"][-1]["at_phrase"] > 0 and "s1" not in sh
   and abs(sh["P1"]["t1"] - VO_G2) < 0.2,
   "PR07 presenter speaks the whole hook group (≤ 7.5 s) and the call to action (starting at the phrase that keeps it ≤ 7.5 s); clips carry the middle")

# ---------------------------------------------------------------- Kaggle run
work = ROOT / "job" / "presenter"; work.mkdir(parents=True)
track = work / "vo.wav"; ff("-f", "lavfi", "-i", "sine=frequency=220:duration=40", "-ar", "48000", str(track))
logs, traces = [], []
shots = [{"id": "P1", "role": "hook", "t0": 0.0, "need": 5.2, "track": track}, {"id": "P2", "role": "cta", "t0": 30.0, "need": 6.0, "track": track}]
made = presenter.make(shots, "studio", work, logs.append, lambda *a: traces.append(a), lambda: None)
calls = [json.loads(l) for l in open(ROOT / "kaggle.log")]
pushes = [c for c in calls if c[:2] == ["kernels", "push"]]
jobA = json.load(open(K / "it" / "ds" / "job.json"))
cap = cv2.VideoCapture(made["P1"]["file"])
ok(len(pushes) == 2 and {Path(c[3]).parent.name for c in pushes} == {"it", "itb"} and set(made) == {"P1", "P2"}
   and jobA["items"][0]["audio"] == "P1.wav" and jobA["items"][0]["image"].startswith("shot_P1") and "female airline pilot" in jobA["prompt"]
   and jobA["quant_file"] == "infinitetalk_single_int8_lora" and not (K / "it" / "ds" / "old.wav").exists() and (K / "it" / "ds" / "patch_infinitetalk.py").exists(),
   "PR08 both shots render at once on kernels A and B; each gets its own audio, picture and prompt; the kernel's own files stay")
ok(int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) == 1296 and int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) == 2304 and abs(cap.get(cv2.CAP_PROP_FRAME_COUNT) / 60 - 5.2) < 0.1
   and presenter.budget()["used_h"] > 0, "PR09 clips come back as 1296x2304 60 fps for the full shot length, and their GPU time is counted")
n0 = len(calls); presenter.make(shots, "studio", work, logs.append, lambda *a: None, lambda: None)
ok(len([1 for _ in open(ROOT / "kaggle.log")]) == n0, "PR10 after a restart, finished shots are never rendered (or paid for) again")
os.environ["FAKE_KAGGLE_MODE"] = "error"; w2 = ROOT / "job2"; w2.mkdir()
try:
    presenter.make(shots[:1], "fast", w2, logs.append, lambda *a: None, lambda: None); raised = False
except presenter.Unavailable as e:
    raised = "ERROR" in str(e)
os.environ["FAKE_KAGGLE_MODE"] = "ok"
ok(raised, "PR11 a failed Kaggle run is reported (the reel then uses the classic style)")

# ---------------------------------------------------------------- reel falls back cleanly
class DB:
    def __init__(self): self.ups = []
    def select(self, *a, **k): return []
    def insert(self, *a, **k): return [{}]
    def update(self, t, m, v): self.ups.append(v); return [{"id": "x"}]
j = {"id": str(uuid.uuid4()), "kind": "reel", "status": "running", "brand": "weone", "input": {"video": "presenter_fast"}, "output": {}, "lease_owner": "mac"}
cx = JobContext(DB(), j, "mac"); presenter.PACK = ROOT / "nowhere"
files, sp = reel._presenter_shots(cx, SPEC, VO, "fast")
ok(files == {} and sp is SPEC and cx.data.get("presenter_off") and reel._style(cx) == "fast", "PR12 no character on the Mac: the reel is made in the classic style, with the reason logged")
presenter.PACK = PACKD

# ---------------------------------------------------------------- person mask + words behind the presenter
clip = ROOT / "p.mp4"; ff("-f", "lavfi", "-i", "color=c=0x3080ff:size=1296x2304:rate=60:duration=1", "-pix_fmt", "yuv420p", str(clip))
def seg(fr):
    m = np.zeros(fr.shape[:2], np.uint8); m[:, : fr.shape[1] // 2] = 255; return m     # "person" = left half
mp = matte.make(clip, ROOT / "p_matte.mp4", seg)
ok(mp and int(cv2.VideoCapture(str(mp)).get(cv2.CAP_PROP_FRAME_COUNT)) == 60 and matte.make(clip, ROOT / "none.mp4") in (None, ROOT / "none.mp4"),
   "PR13 person mask video matches the clip frame for frame; without Apple Vision it quietly returns nothing")
logo = ROOT / "logo.png"; cv2.imwrite(str(logo), np.full((100, 100, 4), 255, np.uint8))
TL = {"fonts_dir": os.environ["REELSTUDIO_FONTS_DIR"], "logo": str(logo), "captions": [], "groups": [], "vo_end": 2.0, "end": 3.0, "note_range": [0, 0], "mood": "x",
      "shots": [{"id": "P1", "file": str(clip), "matte": str(mp), "presenter": True, "t0": 0, "t1": 1, "z0": 1.0, "z1": 1.0, "need": 1}],
      "graphics": [{"type": "behind_text", "t0": 0.0, "t1": 1.0, "params": {"text": "WWWWWWWW", "color": "white", "style": "bold", "size": 300, "y": 0.5}},
                   {"type": "big_text", "t0": 0.0, "t1": 1.0, "params": {"text": "FACE", "y": 1500}},
                   {"type": "ui_card", "t0": 0.0, "t1": 1.0, "params": {"title": "DGCA rule", "body": "200 hours for a CPL", "icon": "check", "side": "right"}}]}
out = ROOT / "frames.mp4"; compose.render(TL, 30, 32, str(out))
fr = cv2.VideoCapture(str(out)); fr.read(); okf, im = fr.read()
band = im[820:940]                       # the behind_text row (baseline at y = 0.5 H)
left_white = (band[:, 100:500].min(axis=2) > 200).mean(); right_white = (band[:, 600:1000].min(axis=2) > 200).mean()
ok(okf and left_white < 0.02 and right_white > 0.05, "PR14 the keyword sits behind the person (hidden on the masked half, visible on the other)")
ok(im[985:1105, 965:1000].mean() > 200 and (im[1400:1600, 300:800].min(axis=2) > 200).mean() < 0.02,
   "PR15 the floating card shows at mid-height on its side, and centre cards are kept off the presenter")

# ---------------------------------------------------------------- routing and script rules
k_, inp = crm.job_for({"id": 5, "kind": "reel", "title": "t", "slot": "2026-11-24T08:00:00Z", "brief": {"format": "presenter_studio"}})
k2, inp2 = crm.job_for({"id": 6, "kind": "reel", "title": "t", "slot": "2026-11-24T08:00:00Z", "brief": {"format": "nonsense"}})
pp = brain.prompt("presenter", style="fast")
ok(inp.get("video") == "presenter_studio" and "video" not in inp2 and "fake urgency" in pp and "Style: fast" in pp,
   "PR16 the CRM planner picks the presenter per reel; the script rules for presenter reels are premium and honest")

print(f"\n{sum(R)}/{len(R)} passed")
sys.exit(0 if all(R) else 1)

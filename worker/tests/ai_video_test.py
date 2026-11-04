"""AI Video (hybrid): presenter speaks the hook and the call to action, stock clips carry the middle.
python tests/ai_video_test.py   (no Phosphene needed: renders are faked)"""
import json, os, sys, tempfile, uuid
from pathlib import Path
os.environ["REELSTUDIO_ROOT"] = tempfile.mkdtemp(); os.environ["REELSTUDIO_BRAIN"] = "mock"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reelworker.jobs import reel  # noqa: E402
from reelworker.media import footage, presenter, timeline  # noqa: E402
from reelworker.runner import JobContext, TransientError  # noqa: E402
R = []
def ok(c, n): R.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n)

# 4 voice parts: hook 3.2 s, two middle parts, CTA 3.0 s
DUR = {"g1": 3.2, "g2": 6.0, "g3": 7.0, "g4": 3.0}
VO = {g: {"file": f"{g}.wav", "trim": [0.0, d], "spans": [[0.0, d / 2], [d / 2, d]]} for g, d in DUR.items()}
SPEC = {"title": "Pre-flight checks", "groups": [{"id": g, "phrases": [{"caption": f"{g}a"}, {"caption": f"{g}b"}]} for g in DUR],
        "shots": [{"id": "s1", "query": "cockpit", "at_group": "g1", "at_phrase": 0},
                  {"id": "s2", "query": "walkaround", "at_group": "g1", "at_phrase": 1},
                  {"id": "s3", "query": "tyre check", "at_group": "g2", "at_phrase": 0},
                  {"id": "s4", "query": "fuel gauge", "at_group": "g3", "at_phrase": 0},
                  {"id": "s5", "query": "checklist", "at_group": "g3", "at_phrase": 1},
                  {"id": "s6", "query": "takeoff", "at_group": "g4", "at_phrase": 0}], "graphics": []}

a = reel.ai_spec(SPEC, VO); ids = [s["id"] for s in a["shots"]]
ok(ids[0] == "P1" and ids[-1] == "P2", f"presenter opens and closes the reel: {ids}")
ok("s1" not in ids and "s2" not in ids, "hook stock shots replaced by the presenter")
ok("s6" not in ids, "call-to-action stock shot replaced by the presenter")
ok(ids[1:-1] == ["s3", "s4", "s5"], "middle stock shots kept in order")
TL = timeline.build(a, VO, "", "")
sh = {s["id"]: s for s in TL["shots"]}
ok(sh["P1"]["t0"] == 0.0 and abs(sh["P1"]["t1"] - (TL["groups"][1]["t0"] - 0.08)) < 0.01, "P1 covers exactly the hook")
ok(abs(sh["P2"]["t0"] - (TL["groups"][-1]["t0"] - 0.08)) < 0.01 and sh["P2"]["t1"] == TL["end"], "P2 runs from the CTA to the end")
ok(sh["P1"]["need"] <= 10.04 and sh["P2"]["need"] - 2.4 <= 10.04, "each presenter take fits one render (<= 10 s of speech)")

# long hook: the presenter hands over to a clip by ~9.6 s instead of freezing
LONG = {**VO, "g1": {"file": "g1.wav", "trim": [0.0, 14.0], "spans": [[0, 4], [4, 9], [9, 14]]}}
SL = {**SPEC, "groups": [{"id": "g1", "phrases": [{"caption": "a"}, {"caption": "b"}, {"caption": "c"}]}] + SPEC["groups"][1:],
      "shots": SPEC["shots"][:1] + [{"id": "s2", "query": "walkaround", "at_group": "g1", "at_phrase": 2}] + SPEC["shots"][2:]}
al = reel.ai_spec(SL, LONG); tl = timeline.build(al, LONG, "", "")
p1 = next(s for s in tl["shots"] if s["id"] == "P1")
ok(p1["t1"] <= 9.7, f"long hook: presenter take ends by 9.7 s ({p1['t1']})")

ok(presenter.frames_for(3.0) % 8 == 1 and presenter.frames_for(3.0) / 24 >= 3.0, "frame count on LTX's 8k+1 grid and covers the shot")
ok(presenter.frames_for(30) == 241, "frame count capped at 241 (10 s)")

# stage_footage in AI mode: presenter takes made, stock search only for the middle
SEARCHES, MADE = [], []
footage.search = lambda q, per_source=6: (SEARCHES.append(q), [{"src": "pexels", "id": f"{q[:4]}{i}", "q": q, "author": "x", "page": "", "licence": "Pexels"} for i in range(3)])[1]
footage.contact_sheets = lambda shots, cands, work, font: None
footage.download = lambda c, dst: (Path(dst).parent.mkdir(parents=True, exist_ok=True), Path(dst).write_text("v"), Path(dst))[2]
footage.prep_shot = lambda src, dst, a_, b, c: (Path(dst).write_text("v"), Path(dst))[1]
presenter.available = lambda: (True, "")
presenter.voice_track = lambda groups, end, out: out
presenter.make = lambda shots, work, log, trace, cc: (MADE.extend(shots), {s["id"]: {"file": f"{s['id']}.mp4", "sync": 0.31, "seed": 101, "sec": 600} for s in shots})[1]
reel._vo_map = lambda ctx: VO
reel.brandmod.logo = lambda p, db=None: "logo.png"
reel._brand = lambda ctx: {"name": "We One Aviation", "niche": "aviation"}
reel._pick = lambda ctx, text, work: [{"shot_id": s, "candidate": f"{s}-0"} for s in ("s3", "s4", "s5")]

class DB:
    def __init__(self): self.out = {}
    def select(self, *a, **k): return []
    def update(self, *a, **k): return None
    def insert(self, *a, **k): return {}
    def rpc(self, *a, **k): return None

def ctx_for(video):
    c = JobContext(DB(), {"id": str(uuid.uuid4()), "kind": "reel", "brand": "weone", "input": {"concept": {"title": "t"}, "video": video}, "output": {}}, "test")
    c.path("spec.json").write_text(json.dumps(SPEC)); c.trace = lambda *a, **k: None; c.log = lambda *a, **k: None
    c.merge_output = lambda v: c.job["output"].update(v); return c

c = ctx_for("ai"); reel.stage_footage(c, lambda f: None)
fj = json.loads(c.path("footage.json").read_text())
ok([m["id"] for m in MADE] == ["P1", "P2"] and {m["role"] for m in MADE} == {"hook", "cta"}, "two presenter takes rendered (hook, CTA)")
ok(set(fj["files"]) == {"P1", "s3", "s4", "s5", "P2"}, f"footage = presenter + middle clips: {sorted(fj['files'])}")
ok(not any(q in SEARCHES for q in ("cockpit", "walkaround", "takeoff")), "no stock search for presenter slots")
ok(c.job["output"]["ai_video"]["presenter"]["P1"]["sync"] == 0.31, "lip-sync scores reported on the review")
TLb, _ = reel.build_timeline(c)
ok([s["id"] for s in TLb["shots"]][0] == "P1" and TLb["shots"][0]["file"] == "P1.mp4", "render timeline opens on the presenter")

MADE.clear(); SEARCHES.clear(); c2 = ctx_for("stock"); reel.stage_footage(c2, lambda f: None)
ok(not MADE and not c2.path("spec_ai.json").exists(), "stock mode untouched: no presenter, no AI spec")

presenter.available = lambda: (False, "Phosphene not found (SSD unplugged)")
try:
    reel.stage_footage(ctx_for("ai"), lambda f: None); ok(False, "AI Video without Phosphene waits with a reason")
except TransientError as e:
    ok("SSD" in str(e), f"AI Video without Phosphene waits with a reason: {e}")

print(f"\n{sum(r[0] for r in R)}/{len(R)} passed"); sys.exit(0 if all(r[0] for r in R) else 1)

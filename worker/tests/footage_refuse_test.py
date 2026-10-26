"""Clip picking: when Claude says no candidate fits, search again with its query; never fall back to a clip it refused.
python tests/footage_refuse_test.py   (fake search/download, scripted Claude)"""
import json, os, sys, tempfile, uuid
from pathlib import Path
os.environ["REELSTUDIO_ROOT"] = tempfile.mkdtemp(); os.environ["REELSTUDIO_BRAIN"] = "mock"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reelworker.jobs import reel  # noqa: E402
from reelworker.media import footage, timeline  # noqa: E402
from reelworker.runner import JobContext  # noqa: E402
R = []
def ok(c, n): R.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n)
SEARCHES = []
footage.search = lambda q, per_source=6: (SEARCHES.append(q), [{"src": "pexels", "id": f"{q[:6]}{i}", "q": q} for i in range(3)])[1]
footage.contact_sheets = lambda shots, cands, work, font: None
footage.download = lambda c, dst: (Path(dst).parent.mkdir(parents=True, exist_ok=True), Path(dst).write_text(c["q"]), Path(dst))[2]
footage.prep_shot = lambda src, dst, a, b, c: (Path(dst).write_text(Path(src).read_text()), Path(dst))[1]
footage.credits = lambda chosen: []
timeline.build = lambda spec, vo, fonts, logo: {"shots": [{"id": s["id"], "need": 3.0} for s in spec["shots"]]}
reel._vo_map = lambda ctx: {}
reel.brandmod.logo = lambda p, db=None: "logo.png"
reel._brand = lambda ctx: {"name": "Book My Charter", "niche": "private jet charter", "visual_style": {"look": "premium", "footage_rules": ["no airline rows"]}}
reel.brandmod.legacy = lambda p: False
ROUNDS = []
class Brain:
    def prompt(self, name, **v): return json.dumps(v)
    def ask(self, name, text, schema, tools, cwd, timeout=0, on_event=None):
        ROUNDS.append(json.loads(text.split("---\n\n", 1)[-1]))
        if len(ROUNDS) == 1:
            return {"picks": [{"shot_id": "s1", "candidate": "none", "new_query": "jet exterior night"},
                              {"shot_id": "s2", "candidate": "none", "new_query": "empty business jet interior"},
                              {"shot_id": "s3", "candidate": "s3-1"}, {"shot_id": "s4", "candidate": "none", "alternates": ["s4-1"]}]}
        return {"picks": [{"shot_id": "s1", "candidate": "none"}, {"shot_id": "s2", "candidate": "s2-2"}]}
reel.brain = reel.agents.brain = Brain()
class DB:
    def update(self, *a, **k): return [{}]
    def insert(self, *a, **k): return [{}]
    def select(self, *a, **k): return []
j = {"id": str(uuid.uuid4()), "kind": "reel", "brand": "bookmycharter", "input": {}, "output": {}, "parent_id": None}
ctx = JobContext(DB(), j, "mac"); ctx.trace = lambda *a, **k: None; ctx.merge_output = lambda *a, **k: None
spec = {"shots": [{"id": f"s{i}", "query": f"q{i}", "at_group": "G1", "at_phrase": i - 1} for i in range(1, 5)], "groups": []}
ctx.path("spec.json").write_text(json.dumps(spec))
reel.stage_footage(ctx, lambda f: None)
fj = json.loads(ctx.path("footage.json").read_text())
ok("jet exterior night" in SEARCHES and "empty business jet interior" in SEARCHES, "refused shots are searched again with Claude's better query")
ok(len(ROUNDS) == 2 and "s2" in ROUNDS[1]["shots"] and "s3" not in ROUNDS[1]["shots"], "only the refused shots go to a second pick")
ok(fj["chosen"]["s2"]["q"] == "empty business jet interior", "the second pick uses a clip from the new search")
ok(fj["chosen"]["s3"]["label"] == "s3-1", "accepted picks are kept")
ok("s4" not in fj["files"], "a shot with no fitting clip is left out, never filled with a refused clip")
ok("s1" in fj["files"], "the opening shot always has a clip")
ok("no airline rows" in ROUNDS[0]["look"], "the account's footage rules reach the picker")
bad = [n for k, n in R if not k]; print(f"{len(R) - len(bad)}/{len(R)} clip-picking checks passed"); sys.exit(1 if bad else 0)

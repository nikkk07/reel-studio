"""Autopilot worst cases with a fake database and a scripted Claude (no network).   python tests/autopilot_test.py"""
import json, os, sys, tempfile, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
os.environ["REELSTUDIO_ROOT"] = tempfile.mkdtemp(); os.environ["REELSTUDIO_BRAIN"] = "mock"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reelworker.runner import JobContext, Cancelled  # noqa: E402
from reelworker.jobs import autopilot, reel, suggest  # noqa: E402
from reelworker import schemas  # noqa: E402

R = []
def ok(c, n): R.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n)


class DB:
    def __init__(self, rows=None): self.rows = {r["id"]: r for r in (rows or [])}; self.events = []
    def _m(self, r, q):
        for part in q.split("&"):
            if "=" not in part or part.startswith(("select=", "order=", "limit=")): continue
            k, v = part.split("=", 1); op, val = v.split(".", 1)
            if k.startswith("input->>"): got = str((r.get("input") or {}).get(k[8:]))
            elif k == "created_at": continue
            elif k == "brand": got = str(r.get("brand") or "weone")      # the real table defaults every job to We One Aviation
            else: got = str(r.get(k))
            if op == "eq" and got != val: return False
        return True
    def select(self, t, q=""):
        if t != "jobs": return []
        lim = int(next((p[6:] for p in q.split("&") if p.startswith("limit=")), 999))
        return [r for r in sorted(self.rows.values(), key=lambda r: r["created_at"], reverse=True) if self._m(r, q)][:lim]
    def insert(self, t, row, upsert=False):
        if t == "job_events": self.events.append(row); return [row]
        row = {"id": str(uuid.uuid4()), "status": "queued", "output": {}, "parent_id": None, "created_at": datetime.now(timezone.utc).isoformat(), **row}
        self.rows[row["id"]] = row; return [row]
    def update(self, t, match, vals):
        jid = match.split("id=eq.")[1].split("&")[0]; r = self.rows.get(jid)
        if not r or ("status=eq.running" in match and r["status"] != "running"): return []
        r.update(vals); return [r]


class Brain:   # scripted Claude: scout from the fixture, judge scores by title
    def __init__(self, judge_by_title=None, unsafe=()): self.vars = {}; self.calls = []; self.jt = judge_by_title or {}; self.unsafe = unsafe
    def prompt(self, name, **v): self.vars[name] = v; return name
    def ask(self, name, text, schema, tools, cwd, timeout=0, on_event=None):
        self.calls.append((name, tools))
        if name == "autopilot_scout": return json.loads((Path(__file__).parent / "fixtures/autopilot_scout.json").read_text())
        cands = json.loads(self.vars["autopilot_judge"]["candidates"])
        return {"summary": "s", "scores": [{"index": c["index"], "safe": c["title"] not in self.unsafe, "note": f"n-{c['title'][:10]}",
                **{k: self.jt.get(c["title"], 5) for k in schemas.CRITERIA}} for c in cands]}


def job(db, inp, **kw):
    j = {"id": str(uuid.uuid4()), "kind": "suggest", "status": "running", "input": inp, "output": {}, "lease_owner": "mac",
         "created_at": datetime.now(timezone.utc).isoformat(), **kw}
    db.rows[j["id"]] = j; return JobContext(db, j, "mac")


today = datetime.now(autopilot.IST).date().isoformat()
past = {"id": "p1", "kind": "reel", "status": "done", "created_at": "2026-10-21T08:00:00+00:00", "parent_id": None,
        "input": {"concept": {"title": "7700, 7600, 7500: Yeh 3 numbers dabate hi ATC alert", "hook": "h", "category": "emergency"}, "autopilot": "old"},
        "output": {"title": "Squawk 7700 ka matlab aur 7600 7500", "ig": {"state": "posted", "posted_at": "2026-10-22T10:56:12Z"},
                   "ig_stats": {"views": 1500, "reach": 1200, "likes": 60, "comments": 5, "shares": 14, "saved": 9, "avg_watch_s": 11.2, "hours_since_post": 20}}}

# 1. normal run: best weighted topic wins, reel queued with its concept, scoreboard kept
db = DB([dict(past)]); b = Brain({"CPL ke liye DGCA ke 6 exams": 9, "Winter schedule se flight time kyun badalta hai": 6}); autopilot.brain = b
ctx = job(db, {"autopilot": True, "day": today, "trigger": "schedule"}); suggest.run(ctx)
reels = [r for r in db.rows.values() if r["kind"] == "reel" and r["id"] != "p1"]
pick = ctx.job["output"]["autopilot"]
ok(ctx.job["status"] == "done" and len(reels) == 1 and pick["title"] == "CPL ke liye DGCA ke 6 exams", f"winner = highest independent score, one reel queued ({pick['title']}, {pick['score']})")
ok(reels[0]["input"]["autopilot"] == ctx.id and reels[0]["input"]["concept"]["category"] == "career" and reels[0]["input"]["concept"]["sources"], "reel carries the concept, sources, category and the run id")
ok([c[0] for c in b.calls] == ["autopilot_scout", "autopilot_judge"] and b.calls[0][1] == ["WebSearch", "WebFetch"] and b.calls[1][1] == [], "two Claude calls: research with web tools, judging with none")
ok("1500" in b.vars["autopilot_scout"]["performance"] and "sends 14" in b.vars["autopilot_scout"]["performance"], "our own Instagram results are fed into the research")
ok(len(ctx.job["output"]["topics"]) == 6 and ctx.job["output"]["topics"][0]["title"] == pick["title"], "all 6 scored topics are kept, best first")
ok(any(t.get("repeat") for t in ctx.job["output"]["topics"] if "7700" in t["title"]), "a near-copy of a recent reel is dropped before judging")
cands = json.loads(b.vars["autopilot_judge"]["candidates"]); ok(not any("7700" in c["title"] for c in cands), "the judge never sees the repeat")
# 2. crash after queuing -> no second reel
ctx.job["status"] = "running"; db.rows[ctx.id]["status"] = "running"; ctx.job["output"]["autopilot"] = pick
autopilot.run(ctx); ok(len([r for r in db.rows.values() if r["kind"] == "reel" and r["id"] != "p1"]) == 1, "restart after the reel was queued never makes a second one")
# 3. unsafe topics are never picked; all unsafe -> retry
db = DB([dict(past)]); autopilot.brain = Brain({"CPL ke liye DGCA ke 6 exams": 10}, unsafe=("CPL ke liye DGCA ke 6 exams",))
ctx = job(db, {"autopilot": True, "day": today, "trigger": "manual"}); suggest.run(ctx)
ok(ctx.job["output"]["autopilot"]["title"] != "CPL ke liye DGCA ke 6 exams", "a topic the editor marks unsafe is never picked, even with the top score")
db = DB(); autopilot.brain = Brain(unsafe=tuple(t["title"] for t in json.loads((Path(__file__).parent / "fixtures/autopilot_scout.json").read_text())["topics"]))
ctx = job(db, {"autopilot": True, "day": today, "trigger": "manual"})
try: suggest.run(ctx); ok(False, "all unsafe -> error")
except RuntimeError as e: ok("unsafe" in str(e) and not [r for r in db.rows.values() if r["kind"] == "reel"], "every topic unsafe -> no reel, the run retries with new research")
# 4. a scheduled run the Mac sees only the next day is skipped
db = DB(); autopilot.brain = Brain(); y = (datetime.now(autopilot.IST) - timedelta(days=1)).date().isoformat()
ctx = job(db, {"autopilot": True, "day": y, "trigger": "schedule"}); suggest.run(ctx)
ok(ctx.job["output"]["autopilot"].get("skipped") == "missed" and not autopilot.brain.calls and not [r for r in db.rows.values() if r["kind"] == "reel"], "yesterday's missed run is skipped (no surprise reel, no Claude use)")
# 5. normal suggestions are untouched
ok(suggest.run.__code__.co_names.count("autopilot") >= 1, "manual 'Suggest topics' still uses its own path")

# 6. quality gate for autopilot reels
def reel_ctx(db, inp, qc):
    j = {"id": str(uuid.uuid4()), "kind": "reel", "status": "running", "input": inp, "output": {"qc": qc, "title": "T"}, "lease_owner": "mac",
         "created_at": datetime.now(timezone.utc).isoformat(), "parent_id": None}
    db.rows[j["id"]] = j; c = JobContext(db, j, "mac"); c.state["done"] = [s[0] for s in reel.STAGES]; return c
good = {"score": 6, "pass": False, "loudness_ok": True, "size_ok": True, "duration_ok": True, "summary": "fine", "issues": []}
db = DB(); c = reel_ctx(db, {"autopilot": "r1", "concept": {"title": "T"}}, good); reel.run(c)
ok(c.job["status"] == "review" and not c.job["output"].get("autopilot_hold"), "score 6/10 (our usual) -> review, posts by itself after the window")
bad = {**good, "score": 4, "issues": [{"t": 3.0, "problem": "wrong aircraft on screen"}]}
db = DB(); c = reel_ctx(db, {"autopilot": "r1", "concept": {"title": "T"}}, bad); reel.run(c)
kids = [r for r in db.rows.values() if r.get("parent_id") == c.id]
ok(c.job["status"] == "cancelled" and len(kids) == 1 and "wrong aircraft" in kids[0]["input"]["notes"] and kids[0]["input"]["autopilot"] == "r1", "score 4/10 -> one automatic fixed version with the problems as notes")
c.job["status"] = "running"; db.rows[c.id]["status"] = "running"; reel.run(c)
ok(len([r for r in db.rows.values() if r.get("parent_id") == c.id]) == 1, "a restart never queues a second fix")
db = DB(); c = reel_ctx(db, {"autopilot": "r1", "concept": {"title": "T"}, "notes": "Automatic fix"}, bad); reel.run(c)
ok(c.job["status"] == "review" and c.job["output"].get("autopilot_hold") and "Not posted automatically" in c.job["output"]["autopilot_note"], "still weak after the fix -> held for a person, never auto-posted")
db = DB(); c = reel_ctx(db, {"autopilot": "r1", "concept": {"title": "T"}}, {**good, "score": 8, "loudness_ok": False}); reel.run(c)
ok(c.job["status"] == "cancelled" and "loudness_ok" in c.job["output"]["notes"], "a failed loudness/size/length check triggers the fix even with a good score")
db = DB(); c = reel_ctx(db, {"concept": {"title": "T"}}, bad); reel.run(c)
ok(c.job["status"] == "review" and not c.job["output"].get("autopilot_hold"), "manual reels are unchanged (you review them)")
# 7. engagement math
ok(autopilot.engagement({"reach": 1000, "shares": 10, "saved": 5, "comments": 3, "likes": 50}) == 11.1 and autopilot.engagement({}) is None, "engagement per 100 reached weights sends > saves > comments > likes")
ok(autopilot.similar("Squawk 7700 ka matlab", "7700, 7600, 7500: squawk codes") >= 0.6 and autopilot.similar("CPL exams DGCA", "Lightning strike plane") == 0, "repeat detector")
# 8. abuse / abnormal cases
db = DB(); db.settings_off = True; autopilot.brain = Brain()
_sel = DB.select
def sel_off(self, t, q=""):
    return [{"value": {"on": False}}] if t == "settings" else _sel(self, t, q)
DB.select = sel_off
ctx = job(db, {"autopilot": True, "day": today, "trigger": "schedule"}); suggest.run(ctx)
ok(ctx.job["output"]["autopilot"].get("skipped") == "off" and not autopilot.brain.calls, "switched off after the cloud queued the run -> skipped, no Claude use, no reel")
ctx = job(db, {"autopilot": True, "day": today, "trigger": "manual"}); suggest.run(ctx)
ok(ctx.job["output"]["autopilot"].get("reel_id"), "Run now still works while the daily schedule is off")
DB.select = _sel
fx = json.loads((Path(__file__).parent / "fixtures/autopilot_scout.json").read_text())
fx["topics"][0]["title"] = "Plane crash ke victims aur death toll"; fx["topics"][0]["viral_score"] = 100
class B2(Brain):
    def ask(self, name, *a, **k):
        if name == "autopilot_scout": self.calls.append((name, a[2])); return fx
        return Brain.ask(self, name, *a, **k)
db = DB(); autopilot.brain = B2({"Plane crash ke victims aur death toll": 10}); ctx = job(db, {"autopilot": True, "day": today, "trigger": "manual"}); suggest.run(ctx)
ok("death toll" not in ctx.job["output"]["autopilot"]["title"] and any(t.get("editor_note") == "skipped: sensitive topic" for t in ctx.job["output"]["topics"]), "a tragedy topic is never auto-picked, even with the top score")
from reelworker.runner import TransientError
from reelworker import instagram
spec = {"title": "T", "groups": [{"id": "G1", "phrases": [{"say": "abhi visit karo cheapflights.xyz", "caption": "x"}]}], "shots": [{"id": "S1", "query": "q", "at_group": "G1", "at_phrase": 0}], "graphics": [], "claims": []}
try: reel.validate_spec(spec); ok(False, "leak")
except TransientError as e: ok("cheapflights.xyz" in str(e), "a web address planted in the script (prompt injection) -> script is written again")
spec["groups"][0]["phrases"][0]["say"] = "35000 feet par Boeing 737-800, 2019 - 2024"; ok(reel.validate_spec(spec)["groups"], "normal numbers, aircraft names and year ranges pass")
cap = instagram.compose("Book now at www.x.xyz, call +91 98765 43210, DM @rival_page. Follow @we_one_aviation #a #b #c #d #e #f", ["aviation", "pilot"])
ok("www" not in cap and "98765" not in cap and "@rival_page" not in cap and "@we_one_aviation" in cap and cap.count("#") == 5, "caption: links, phone numbers and other accounts removed, max 5 hashtags")

bad_ = [n for k, n in R if not k]
print(f"{len(R) - len(bad_)}/{len(R)} autopilot checks passed" + (" | FAILED: " + "; ".join(bad_) if bad_ else "")); sys.exit(1 if bad_ else 0)

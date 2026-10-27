"""Self-learning loop + Account Intelligence: lessons with baselines, automatic rollback/proving, Analyst gating,
Resource Manager, and the account study (profile merge, people's decisions kept, setup -> live, logo).
python tests/learning_test.py   (in-memory PostgREST stand-in, scripted Claude)"""
import json, os, re, sys, tempfile, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
os.environ["REELSTUDIO_ROOT"] = tempfile.mkdtemp(); os.environ["REELSTUDIO_BRAIN"] = "mock"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reelworker import agents, config  # noqa: E402
from reelworker import brand as brandmod  # noqa: E402
from reelworker.runner import JobContext  # noqa: E402
from reelworker.jobs import learn, onboard  # noqa: E402
R = []
def ok(c, n): R.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n)
NOW = datetime.now(timezone.utc)
iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")

class DB:
    """Tiny PostgREST: eq / in / is.null / gte / lt / or=(a.eq.x,a.is.null) / order desc / limit."""
    def __init__(self): self.t = {"jobs": [], "agent_runs": [], "agent_lessons": [], "brands": [], "job_events": []}; self.seq = 0
    def _get(self, r, k):
        if "->>" in k: a, b = k.split("->>"); return str((r.get(a) or {}).get(b))
        return r.get(k)
    def _cond(self, r, k, v):
        op, _, val = v.partition(".")
        got = self._get(r, k)
        if op == "eq": return str(got) == val
        if op == "in": return str(got) in val.strip("()").split(",")
        if op == "is": return got is None
        if op == "gte": return got is not None and str(got) >= val
        if op == "lt": return got is not None and str(got) < val
        return True
    def select(self, t, q=""):
        rows = list(self.t[t]); lim = None; order = None
        for part in q.split("&"):
            if not part or part.startswith("select="): continue
            k, _, v = part.partition("=")
            if k == "limit": lim = int(v); continue
            if k == "order": order = v; continue
            if k == "or":
                alts = v.strip("()").split(",")
                rows = [r for r in rows if any(self._cond(r, a.split(".", 1)[0], a.split(".", 1)[1]) for a in alts)]; continue
            rows = [r for r in rows if self._cond(r, k, v)]
        if order:
            col, _, d = order.partition("."); rows.sort(key=lambda r: str(r.get(col)), reverse=d.startswith("desc"))
        return [dict(r) for r in rows[:lim] if True] if lim else [dict(r) for r in rows]
    def insert(self, t, row, upsert=False):
        self.seq += 1; row = {"id": self.seq if t != "jobs" else str(uuid.uuid4()), "created_at": iso(datetime.now(timezone.utc)), **row}
        self.t[t].append(row); return [dict(row)]
    def update(self, t, m, vals):
        out = []
        for r in self.t[t]:
            if all(self._cond(r, *p.split("=", 1)) for p in m.split("&")):
                r.update(vals); out.append(dict(r))
        return out

WP = json.load(open(Path(__file__).resolve().parents[2] / "brands/weone/profile.json"))
def seed(db, effs, start_days=20, critic=80):
    db.t["brands"] = [{"id": "weone", "name": "We One Aviation", "website": "https://weoneaviation.in", "instagram": "we_one_aviation", "logo_asset": None, "profile": WP, "status": "live", "color": "#0A2342", "links": {}},
                      {"id": "newco", "name": "New Co Legal", "website": "https://newco.example.com", "instagram": None, "logo_asset": None, "profile": {}, "status": "setup", "color": "#B8902F", "links": {"linkedin": "https://linkedin.com/company/newco"}}]
    for k, e in enumerate(effs):
        when = NOW - timedelta(days=start_days - k)
        db.t["jobs"].append({"id": f"r{k}", "kind": "reel", "brand": "weone", "status": "done", "created_at": iso(when - timedelta(hours=2)),
                             "input": {"concept": {"title": f"Reel {k}", "lane": "core" if k % 2 else "trending"}},
                             "output": {"title": f"Reel {k}", "critic": {"total": critic}, "ig": {"posted_at": iso(when)},
                                        "ig_stats": {"reach": 1000, "views": 1500, "shares": e, "saved": 0, "likes": 0, "comments": 0, "hours_since_post": 48}}})

class Brain:
    def __init__(self): self.asks = []
    def prompt(self, name, **v): return name + json.dumps(v)[:20000]
    def ask(self, name, text, schema, tools, cwd, timeout=0, on_event=None):
        self.asks.append(name); self.__dict__.setdefault("texts", {})[name] = text
        if name == "analyst":
            return {"insights": "Core reels with a number in the hook got more sends.", "retire": [],
                    "lessons": [{"agent": "scriptwriter", "lesson": "Open core reels with the exact rule as a number.", "why": "3 of 3 did 2x"}]}
        if name == "resource_manager":
            return {"health": [{"agent": "scriptwriter", "status": "ok", "note": "fine"}], "usage_note": "most usage is research",
                    "lessons": [{"agent": "footage_picker", "lesson": "Reject clips of airliner cabins for private-jet lines.", "why": "QC flagged it 4 times", "metric": "qc"}]}
        if name == "account_intel":
            bank = [{"title": f"Topic {i}", "hook": "h", "lane": "core" if i < 11 else "trending", "source": "https://newco.example.com/p"} for i in range(17)]
            return {"business": "New Co Legal is a law firm in Delhi offering corporate, property and family law services to individuals and companies.",
                    "niche": "legal services", "tone": "calm", "language": "english", "services": ["Corporate law", "Property law"], "usp": ["x"], "audience": ["founders"],
                    "rules": ["No solicitation (Bar Council of India Rule 36): legal awareness only", "No case outcome promises", "No client names"],
                    "market_facts": [], "competitors": [{"name": "A", "note": "b"}], "competitor_gap": "g", "pillars": [{"id": "p", "name": "P", "why": "w", "lane": "core"}],
                    "hook_patterns": ["h"], "topic_bank": bank, "visual_style": {"look": "l", "footage_keywords": ["k"], "avoid": ["a"], "graphics": "g"},
                    "hashtags": ["law"], "lanes": {"core": "c", "trending": "t"}, "lane_names": {"core": "Legal awareness", "trending": "Trending"},
                    "core_keywords": ["law"], "strategy": "Legal awareness reels.", "sources": ["https://newco.example.com"], "end_sub": "law, explained simply"}
        raise AssertionError(name)

def ctx_for(db, kind, brand, inp):
    j = {"id": str(uuid.uuid4()), "kind": kind, "brand": brand, "status": "running", "input": inp, "output": {}, "lease_owner": "mac", "created_at": iso(NOW)}
    db.t["jobs"].append(j); return JobContext(db, j, "mac")

# 1. Analyst waits for enough real results
db = DB(); seed(db, [5, 5]); b = Brain(); learn.brain = agents.brain = b; agents._CACHE.clear(); brandmod._CACHE.clear()
c = ctx_for(db, "learn", "weone", {"brand": "weone", "day": "d"}); learn.run(c)
ok("analyst" not in b.asks and "Only 2 posts" in c.job["output"]["learn"]["insights"], "with only 2 measured reels the Analyst writes no lessons (no guessing)")

# 1b. the account's other Instagram posts (past content, also made outside the studio) count as evidence and reach the Analyst
db = DB(); seed(db, [5, 5]); b = Brain(); learn.brain = agents.brain = b; agents._CACHE.clear(); brandmod._CACHE.clear()
db.t["settings"] = [{"key": "ig_library", "value": {"at": iso(NOW), "posts": [
    {"id": "m1", "at": iso(NOW - timedelta(days=9)), "type": "REELS", "caption": "Why pilots say Roger. Save this", "reach": 900, "views": 1200, "likes": 40, "comments": 6, "shares": 12, "saved": 9},
    {"id": "m2", "at": iso(NOW - timedelta(days=12)), "type": "REELS", "caption": "Old timetable post", "reach": 800, "views": 900, "likes": 5, "comments": 0, "shares": 0, "saved": 0}]}}]
c = ctx_for(db, "learn", "weone", {"brand": "weone", "day": "d"}); learn.run(c)
rep = c.job["output"]["learn"]
ok("analyst" in b.asks and rep["past_posts"] == 2 and rep["past_measured"] == 2, "2 reels + 2 measured past posts: the Analyst starts and reads the past posts")
ok("Why pilots say Roger" in b.texts.get("analyst", ""), "the Analyst sees the account's past captions with their results")

# 2. enough results: lessons are created with a baseline from the reels before them
db = DB(); seed(db, [5, 5, 6, 4, 5]); b = Brain(); learn.brain = agents.brain = b; agents._CACHE.clear(); brandmod._CACHE.clear()
c = ctx_for(db, "learn", "weone", {"brand": "weone", "day": "d", "resources": True})
past = iso(NOW - timedelta(minutes=5))
for k in range(6): db.insert("agent_runs", {"agent": "scriptwriter", "ok": True, "ms": 1000, "grade": 80, "brand": "weone", "created_at": past})
db.insert("agent_runs", {"agent": "quality_inspector", "ok": True, "grade": 60, "brand": "weone", "created_at": past})
learn.run(c)
L = db.t["agent_lessons"]
a = [l for l in L if l["created_by"] == "analyst"][0]
ok(a["agent"] == "scriptwriter" and a["brand"] == "weone" and a["metric"] == "engagement" and a["baseline"] == 2.0 and a["status"] == "active",
   f"Analyst lesson saved for the right agent and account, baseline = median engagement before it ({a['baseline']})")
rm = [l for l in L if l["created_by"] == "resource_manager"][0]
ok(rm["agent"] == "footage_picker" and rm["brand"] is None and rm["metric"] == "qc" and rm["baseline"] == 60.0, "Resource Manager lesson is team-wide with a QC baseline")
ok(c.job["output"]["learn"]["health"] and c.job["output"]["learn"]["usage_note"], "the team's health and usage note are reported")
agents._CACHE.clear()
got = agents.lessons(db, "scriptwriter", "weone")
ok(any("exact rule as a number" in g["lesson"] for g in got) and not agents.lessons(db, "scriptwriter", "bookmycharter"), "the lesson reaches the scriptwriter of this account only")

# 3. after 3 worse reels the lesson is rolled back automatically; after 5 better ones it is proven
for k, e in enumerate([1, 1, 1]):
    when = NOW + timedelta(hours=k + 1)
    db.t["jobs"].append({"id": f"n{k}", "kind": "reel", "brand": "weone", "status": "done", "created_at": iso(when), "input": {"concept": {"title": f"New {k}"}},
                         "output": {"title": f"New {k}", "ig": {"posted_at": iso(when)}, "ig_stats": {"reach": 1000, "shares": e, "hours_since_post": 30}}})
a["created_at"] = iso(NOW)
for l in db.t["agent_lessons"]:
    if l["id"] == a["id"]: l["created_at"] = iso(NOW)
c = ctx_for(db, "learn", "weone", {"brand": "weone", "day": "d2"}); learn.brain = agents.brain = Brain(); learn.run(c)
st = [l for l in db.t["agent_lessons"] if l["id"] == a["id"]][0]
ok(st["status"] == "rolled_back" and st["result"]["after"] < st["result"]["baseline"], f"engagement fell after the lesson -> rolled back automatically ({st['result']})")
agents._CACHE.clear()
ok(not any(g["id"] == a["id"] for g in agents.lessons(db, "scriptwriter", "weone")), "a rolled-back lesson is no longer given to the agent")
for l in db.t["agent_lessons"]:
    if l["id"] == rm["id"]: l["created_at"] = iso(NOW - timedelta(seconds=1))
for k in range(5): db.insert("agent_runs", {"agent": "quality_inspector", "ok": True, "grade": 80, "brand": "weone"})
learn.evaluate(db, "weone", [], print)
ok([l for l in db.t["agent_lessons"] if l["id"] == rm["id"]][0]["status"] == "proven", "QC scores rose after the team lesson -> proven")

# 4. cap: never more than 8 live lessons per agent and account
for k in range(10):
    learn.add_lessons(db, [{"agent": "caption_writer", "lesson": f"Caption lesson number {k} with detail", "why": "w"}], "weone", "analyst", [], "engagement")
live = [l for l in db.t["agent_lessons"] if l["agent"] == "caption_writer" and l["status"] in ("active", "proven")]
ok(len(live) == agents.MAX_LESSONS, f"at most {agents.MAX_LESSONS} live lessons per agent (older ones retired)")

# 5. Account Intelligence: a new account is studied, set live, gets a logo; people's decisions are kept on re-study
onboard.fetch_logo = lambda website, dst: False                                # offline test: placeholder logo
(config.FONTS_DIR).mkdir(parents=True, exist_ok=True)
db = DB(); seed(db, []); b = Brain(); onboard.brain = agents.brain = b; brandmod._CACHE.clear()
c = ctx_for(db, "onboard", "newco", {"brand": "newco"}); onboard.run(c)
nb = [x for x in db.t["brands"] if x["id"] == "newco"][0]; p = nb["profile"]
ok(nb["status"] == "live" and nb.get("intel_at") and p["version"] == 1 and len(p["topic_bank"]) == 17, "a new account is studied and goes live with its own profile")
ok(p["language"] == "english" and p["tts_lang"] == "en" and p["palette"]["accent"] == [184, 144, 47] and p["end_sub"] == "law, explained simply", "voice language, colours and end card are set from the study")
ok(any("Bar Council" in r for r in p["rules"]), "industry advertising rules become hard rules (lawyers: awareness only, no solicitation)")
ok(p["logo_file"] == "brands/newco/logo.png" and (config.LIBRARY / "brands/newco/logo.png").exists(), "the account gets its own logo file (placeholder until one is uploaded in Assets)")
ok(c.job["output"]["previous_profile"] == {} and c.job["output"]["study"]["went_live"], "the previous profile is kept with the study (undo possible)")
nb["profile"] = {**p, "language": "hinglish", "cta": {"say": "x", "caption": "y"}}
c = ctx_for(db, "onboard", "newco", {"brand": "newco"}); onboard.run(c)
p2 = [x for x in db.t["brands"] if x["id"] == "newco"][0]["profile"]
ok(p2["version"] == 2 and p2["language"] == "hinglish" and p2["cta"]["say"] == "x", "a re-study keeps the language and call to action people chose")
w = [x for x in db.t["brands"] if x["id"] == "weone"][0]
c = ctx_for(db, "onboard", "weone", {"brand": "weone"}); onboard.run(c)
pw = [x for x in db.t["brands"] if x["id"] == "weone"][0]["profile"]
ok(pw["legacy_prompts"] is True and pw["language"] == "hinglish" and "logo_file" not in pw and pw["name"] == "We One Aviation", "We One keeps its original Hinglish path and logo after a re-study")
ok(onboard.check_profile({"topic_bank": [], "services": [], "business": "", "rules": [], "sources": []}), "the Supervisor rejects a thin study")

bad = [n for k, n in R if not k]; print(f"{len(R) - len(bad)}/{len(R)} learning checks passed" + (" | FAILED: " + "; ".join(bad) if bad else "")); sys.exit(1 if bad else 0)

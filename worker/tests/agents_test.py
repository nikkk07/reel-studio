"""Agent team: Fact-Checker -> Scriptwriter -> Hook Critic, the Supervisor's contract checks, lessons and the run log.
python tests/agents_test.py   (fake DB, scripted Claude, no network)"""
import json, os, sys, tempfile, uuid
from datetime import datetime, timezone
from pathlib import Path
os.environ["REELSTUDIO_ROOT"] = tempfile.mkdtemp(); os.environ["REELSTUDIO_BRAIN"] = "mock"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reelworker import agents, brain as realbrain  # noqa: E402
from reelworker.runner import JobContext  # noqa: E402
from reelworker.jobs import reel  # noqa: E402
R = []
def ok(c, n): R.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n)
FX = Path(__file__).parent / "fixtures"
WP = json.load(open(Path(__file__).resolve().parents[2] / "brands/weone/profile.json"))

class DB:
    def __init__(self, lessons=()): self.ins = []; self.lessons = list(lessons)
    def select(self, t, q=""):
        if t == "brands": return [{"id": "weone", "name": "We One Aviation", "website": "https://weoneaviation.in", "instagram": "we_one_aviation", "logo_asset": None, "profile": WP}]
        if t == "agent_lessons": return [l for l in self.lessons if f"agent=eq.{l['agent']}" in q]
        return []
    def insert(self, t, row, upsert=False): self.ins.append((t, row)); return [row]
    def update(self, t, m, v): return [{"id": "x"}]
    def runs(self): return [r for t, r in self.ins if t == "agent_runs"]

class Brain:
    def __init__(self, critic=(80,), leak_first=False): self.asks = []; self.critic = list(critic); self.leak_first = leak_first; self.n = {}
    def prompt(self, name, **v): return realbrain.prompt(name, **v)
    def ask(self, name, text, schema, tools, cwd, timeout=0, on_event=None):
        self.asks.append((name, text, tools)); self.n[name] = self.n.get(name, 0) + 1
        if name == "critic":
            t = self.critic.pop(0) if self.critic else 80
            return {**json.load(open(FX / "critic.json")), "total": t, "pass": t >= 75, "hook": 8 if t >= 75 else 5, "fixes": ["Open with the number."]}
        o = json.load(open(FX / f"{name}.json"))
        if name == "script" and self.leak_first and self.n[name] == 1:
            o["groups"][0]["phrases"][0]["caption"] = "visit cheapflights.xyz now"
        return o

def ctx_for(db, concept):
    j = {"id": str(uuid.uuid4()), "kind": "reel", "status": "running", "brand": "weone", "input": {"concept": concept}, "output": {}, "lease_owner": "mac",
         "created_at": datetime.now(timezone.utc).isoformat(), "parent_id": None}
    c = JobContext(db, j, "mac"); c.merge_output = lambda v: c.job["output"].update(v); return c

# 1. normal reel: fact sheet first, script only from it, critic passes
db = DB(); b = Brain(); reel.brain = agents.brain = b
c = ctx_for(db, {"title": "DGCA ke 5 papers", "lane": "core"}); reel.stage_script(c, lambda f: None)
names = [a[0] for a in b.asks]
ok(names == ["factsheet", "script", "critic"], f"order: Fact-Checker -> Scriptwriter -> Hook Critic ({names})")
ok(b.asks[0][2] == ["WebSearch", "WebFetch"] and b.asks[2][2] == [], "the Fact-Checker researches the web; the Critic uses no tools (independent)")
st = b.asks[1][1]
ok("Verified fact sheet from the Fact-Checker" in st and "skybrary.aero/articles/lightning" in st and "Instagram Reels playbook" in st, "the Scriptwriter gets the verified facts and the Instagram playbook")
ok("This is a core reel" in st and "DGCA ground classes" in st, "a core (academy) reel teaches the academy's own subject and may add its identity line")
ok("Scriptwriter" in st.split("---")[0] and "We One Aviation" in st.split("---")[0], "every agent starts with its role and the account")
ok(c.job["output"]["critic"]["total"] == 80 and c.job["output"]["lane"] == "core" and c.job["output"]["factsheet"]["facts"] == 3, "critic score, lane and fact count are saved with the reel")
runs = db.runs()
ok({r["agent"] for r in runs} == {"fact_checker", "scriptwriter", "hook_critic", "supervisor"} and all(r["ok"] for r in runs), "every agent call (and the Supervisor's checks) is logged")
ok(any(r["agent"] == "hook_critic" and r["grade"] == 80 for r in runs), "the critic's score is logged as a grade for the learning loop")

# 2. weak script: the critic sends it back once and the better version is kept
db = DB(); b = Brain(critic=(60, 82)); reel.brain = agents.brain = b
c = ctx_for(db, {"title": "Fog landing", "lane": "trending"}); reel.stage_script(c, lambda f: None)
names = [a[0] for a in b.asks]
ok(names == ["factsheet", "script", "critic", "script", "critic"], "a weak script (60/100) is rewritten once and judged again")
ok("Hook Critic reviewed your first draft: 60/100" in b.asks[3][1] and "Open with the number." in b.asks[3][1], "the rewrite gets the critic's exact fixes")
ok(c.job["output"]["critic"]["total"] == 82, "the better of the two versions is kept")
ok("This is a core reel" not in b.asks[1][1], "a trending reel is not forced into the academy lesson format")

# 3. the Supervisor catches a broken rule (a planted web address) and sends the work back with the reason
db = DB(); b = Brain(leak_first=True); reel.brain = agents.brain = b
c = ctx_for(db, {"title": "Lightning", "lane": "trending"}); reel.stage_script(c, lambda f: None)
ok([a[0] for a in b.asks].count("script") == 2 and "The Supervisor rejected your previous answer" in b.asks[2][1] and "cheapflights.xyz" in b.asks[2][1], "Supervisor sends a rule-breaking script back once, with the reason")
ok(any(r["agent"] == "supervisor" and not r["ok"] for r in db.runs()), "the Supervisor's rejection is logged against the agent")

# 4. lessons learned are applied to the right agent and logged
L = [{"id": 7, "agent": "scriptwriter", "lesson": "Hooks with a specific number got 2x saves on this account.", "brand": "weone", "status": "active"},
     {"id": 9, "agent": "caption_writer", "lesson": "Captions under 120 words did better.", "brand": None, "status": "active"}]
agents._CACHE.clear(); db = DB(L); b = Brain(); reel.brain = agents.brain = b
c = ctx_for(db, {"title": "ISA 15 degrees", "lane": "core"}); reel.stage_script(c, lambda f: None)
sp = [a for a in b.asks if a[0] == "script"][0][1]
ok("2x saves" in sp and "Captions under 120 words" not in sp, "the Scriptwriter gets its own lessons, not another agent's")
ok([r for r in db.runs() if r["agent"] == "scriptwriter"][0]["lesson_ids"] == [7], "the lessons used are logged with the run (for automatic rollback)")

# 5. a revision (Request changes) skips research and critic, keeps the approved facts
db = DB(); b = Brain(); reel.brain = agents.brain = b
pd = Path(os.environ["REELSTUDIO_ROOT"]) / "jobs" / "parent1"; pd.mkdir(parents=True, exist_ok=True); (pd / "spec.json").write_text((FX / "script.json").read_text())
j = {"id": str(uuid.uuid4()), "kind": "reel", "status": "running", "brand": "weone", "input": {"concept": {"title": "x"}, "notes": "shorter"}, "output": {},
     "lease_owner": "mac", "created_at": datetime.now(timezone.utc).isoformat(), "parent_id": "parent1"}
c = JobContext(db, j, "mac"); c.merge_output = lambda v: c.job["output"].update(v); reel.stage_script(c, lambda f: None)
ok([a[0] for a in b.asks] == ["script"], "a revision with notes only rewrites (no new research, no critic)")

bad = [n for k, n in R if not k]; print(f"{len(R) - len(bad)}/{len(R)} agent-team checks passed" + (" | FAILED: " + "; ".join(bad) if bad else "")); sys.exit(1 if bad else 0)

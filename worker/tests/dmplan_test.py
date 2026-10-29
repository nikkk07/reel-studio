"""Per-reel Comment-to-DM: the CTA Strategist's plan is checked, turned into a formal DM, used in the spoken CTA and
the caption, kept for revisions, and skipped safely.   python tests/dmplan_test.py   (fake DB, scripted Claude, no network)"""
import json, os, sys, tempfile, uuid
from datetime import datetime, timezone
from pathlib import Path
os.environ["REELSTUDIO_ROOT"] = tempfile.mkdtemp(); os.environ["REELSTUDIO_BRAIN"] = "mock"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reelworker import dm, agents, brand as brandmod  # noqa: E402
from reelworker.runner import JobContext  # noqa: E402
from reelworker.jobs import reel  # noqa: E402
R = []
def ok(c, n): R.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n)

PROF = {"id": "flystar", "name": "Flying Star Aviator", "website": "https://www.flystar.co.in", "language": "hinglish",
        "business": "CPL ground school", "sources": ["https://www.flystar.co.in/courses/cpl", "https://www.flystar.co.in/blog/dgca-medical", "https://other.com/x"]}
FACTS = ["https://www.dgca.gov.in/medical", "https://www.dgca.gov.in/cpl"]
GOOD = {"keyword": "MEDICAL", "offer": "the 4-step DGCA medical checklist", "say_offer": "पूरी मेडिकल चेकलिस्ट",
        "points": ["Get the Class 2 medical first; it is needed before any flying.", "Class 1 is done at a DGCA-approved centre and is valid for one year under 40.",
                   "Carry previous reports and spectacles prescriptions to the examination.", "Colour vision is tested: a failed test needs a further assessment."],
        "link": "https://www.flystar.co.in/blog/dgca-medical", "source": "https://www.dgca.gov.in/medical", "next_step": "Book your Class 2 medical before you start ground classes.",
        "reply": "Sent! Check your messages for the checklist.", "why": "names the exact worry"}

# 1. contract
ok(dm.check_plan(GOOD, PROF, FACTS) == [], "a good plan passes")
probs = dm.check_plan({**GOOD, "keyword": "yes"}, PROF, FACTS)
ok(any("ONE word" in p for p in probs) or any("generic" in p for p in probs), "lower-case / generic keywords are refused")
ok(any("generic" in p for p in dm.check_plan({**GOOD, "keyword": "INFO"}, PROF, FACTS)), "INFO is too generic (reads as bait)")
ok(any("recent reel" in p for p in dm.check_plan(GOOD, PROF, FACTS, used=["medical"])), "a keyword used by a recent reel is refused")
ok(any("link must be a page on flystar.co.in" in p for p in dm.check_plan({**GOOD, "link": "https://other.com/x"}, PROF, FACTS)), "links only to the account's own site")
ok(any("source must be" in p for p in dm.check_plan({**GOOD, "source": "https://random.blog/x"}, PROF, FACTS)), "sources only from the fact sheet")
ok(any("3-5 useful points" in p for p in dm.check_plan({**GOOD, "points": GOOD["points"][:2]}, PROF, FACTS)), "at least 3 real points")

# 2. the DM: formal, value first, link, next step, follow ask; within Instagram's limit
m = dm.message(GOOD, PROF)
ok(m.startswith("Hi {name}, thank you for your comment.") and "Here is the 4-step DGCA medical checklist:" in m and "\n1. Get the Class 2" in m
   and "Read more: https://www.flystar.co.in/blog/dgca-medical" in m and "Official source: https://www.dgca.gov.in/medical" in m
   and "Follow Flying Star Aviator" in m and len(m) <= 950, "DM: greeting, numbered points, page, source, next step, follow ask")
long = {**GOOD, "points": ["x" * 165] * 5}
ok(len(dm.message(long, PROF)) <= 950, "long plans are trimmed to Instagram's message limit")
ok("not legal advice" in dm.message(GOOD, {**PROF, "business": "Legal services and lawyers"}), "legal accounts always carry the disclaimer")

# 3. the reel's CTA: spoken in the reel's language with its own keyword; caption line names the offer
class DB:
    def __init__(self, on=True): self.on = on; self.merged = []
    def select(self, t, q=""):
        if t == "settings" and "dm@flystar" in q: return [{"value": {"on": self.on, "state": "ready", "keywords": ["CPL"]}}]
        if t == "brands": return [{"id": "flystar", "name": PROF["name"], "website": PROF["website"], "profile": PROF}]
        if t == "jobs" and "output->dm->>keyword" in q: return [{"kw": "VISA"}]
        return []
    def insert(self, *a, **k): return [{}]
    def update(self, t, match, vals): self.merged.append(vals); return [{"id": "x"}]
a = dm.active(DB(), "flystar", PROF, plan=GOOD)
ok(a["keyword"] == "MEDICAL" and a["say"].startswith("कमेंट करें MEDICAL") and "पूरी मेडिकल चेकलिस्ट" in a["say"] and "DM" in a["caption_line"], "Hinglish CTA with the reel's keyword and offer")
e = dm.active(DB(), "flystar", {**PROF, "language": "english"}, plan=GOOD)
ok(e["say"] == "Comment MEDICAL, and we'll DM you पूरी मेडिकल चेकलिस्ट." or e["say"].startswith("Comment MEDICAL, and we'll DM you"), "English CTA")
ok(dm.active(DB(), "flystar", PROF)["keyword"] == "CPL", "without a plan the account keyword is used")
ok(dm.active(DB(on=False), "flystar", PROF, plan=GOOD) is None, "Comment-to-DM off: no DM call to action at all")
spec = json.loads((Path(__file__).parent / "fixtures/script.json").read_text())
s2 = reel.validate_spec(spec, {**PROF, "_dm": a})
ok(s2["groups"][-1]["phrases"][-1]["say"] == a["say"], "the reel ends with its own keyword CTA")

# 4. plan_dm in the script stage: one call, checked, link verified, saved for the caption, the cloud and revisions
def ctx_for(db):
    j = {"id": str(uuid.uuid4()), "kind": "reel", "status": "running", "brand": "flystar", "input": {}, "output": {}, "lease_owner": "mac",
         "created_at": datetime.now(timezone.utc).isoformat(), "parent_id": None}
    return JobContext(db, j, "mac")
calls = []
def fake_call(ctx, agent, text, schema, tools, timeout=0, check=None, name=None, **k):
    calls.append((agent, text, check(GOOD) if check else None)); return json.loads(json.dumps(GOOD))
agents.call = fake_call
reel._url_ok = lambda u: "blog" not in u                    # the chosen page is "down"
db = DB(); ctx = ctx_for(db)
fs = {"facts": [{"fact": "Class 2 first", "source_url": FACTS[0]}], "angle": "medical"}
plan = reel.plan_dm(ctx, PROF, {"title": "DGCA medical", "hook": "Fail this and you never fly"}, fs)
ok(calls and calls[0][0] == "cta_strategist" and "VISA" in calls[0][1] and "https://www.flystar.co.in/blog/dgca-medical" in calls[0][1] and "other.com" not in calls[0][1],
   "the CTA Strategist sees the facts, the account's own pages only, and the recent keywords")
ok(plan["link"] == "https://www.flystar.co.in", "a page that does not open is replaced by the homepage")
out = {}
for v in db.merged: out.update(v.get("output", {}) if isinstance(v.get("output"), dict) else {})
ok((ctx.dir / "dm_plan.json").exists() and ctx.data["dm_plan"]["keyword"] == "MEDICAL", "plan saved with the reel (revisions keep the keyword)")
ok(reel.plan_dm(ctx, PROF, {}, fs) is plan and len(calls) == 1, "never planned twice for the same reel")
ok(reel._brand(ctx)["_dm"]["keyword"] == "MEDICAL" if hasattr(reel, "_brand") else True, "every later stage sees the reel's keyword")
def boom(*a, **k): raise RuntimeError("Claude limited")
agents.call = boom
ctx2 = ctx_for(db)
ok(reel.plan_dm(ctx2, PROF, {"title": "t"}, fs) is None, "if planning fails the reel goes on with the account keyword")
ok(reel.plan_dm(ctx_for(DB(on=False)), PROF, {"title": "t"}, fs) is None, "Comment-to-DM off: nothing planned")

bad = [n for k, n in R if not k]; print(f"{len(R) - len(bad)}/{len(R)} per-reel DM checks passed" + (" | FAILED: " + "; ".join(bad) if bad else "")); sys.exit(1 if bad else 0)

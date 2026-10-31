"""Retention architecture: ranked hooks, open loops, a mid-script re-hook, intent notes, no engagement bait.
python tests/retention_test.py   (no network)"""
import copy, json, os, sys, tempfile
from pathlib import Path
os.environ["REELSTUDIO_ROOT"] = tempfile.mkdtemp(); os.environ["REELSTUDIO_BRAIN"] = "mock"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reelworker import brain, schemas  # noqa: E402
from reelworker.jobs import reel  # noqa: E402
R = []
def ok(c, n): R.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n)

GOOD = json.loads((Path(__file__).parent / "fixtures/script.json").read_text(encoding="utf-8"))
FS = {"facts": [{"fact": "x", "source_url": GOOD["claims"][0]["source_url"]}]}

# 1. engagement bait is refused, honest lines are not
for line in ["Comment YES if you agree", "कमेंट करो 🔥 अगर सही लगा", "Only the real ones will understand", "Like karo agar samjhe",
             "Tag a friend who wants to fly", "Share if this helped", "Drop a ❤️ below", "Double tap if you knew"]:
    sp = copy.deepcopy(GOOD); sp["groups"][1]["phrases"][0]["say"] = line
    ok(reel.engagement_bait(sp), f"bait refused: {line}")
for line in ["Most pilots never tell you this", "Comment MEDICAL, and we'll DM you the checklist.", "Send this to the friend who wants to fly",
             "1 phone can buzz a headset", "Ek galti, ek saal barbaad"]:
    sp = copy.deepcopy(GOOD); sp["groups"][1]["phrases"][0]["say"] = line
    ok(not reel.engagement_bait(sp), f"honest line allowed: {line}")

# 2. the structure: 3 ranked hooks, rank 1 opens the reel, a beat per group with a middle re-hook and a reveal
ok(reel.retention_problems(GOOD) == [], "a complete script passes")
sp = copy.deepcopy(GOOD); sp["hook_options"] = sp["hook_options"][:2]
ok(any("3 ranked hook" in p for p in reel.retention_problems(sp)), "fewer than 3 hook options is refused")
sp = copy.deepcopy(GOOD); sp["hook_options"][0]["rank"], sp["hook_options"][1]["rank"] = 2, 1
ok(any("rank-1 hook" in p for p in reel.retention_problems(sp)), "the script must open with the rank-1 hook")
sp = copy.deepcopy(GOOD); sp["beats"][1]["role"] = "escalation"
ok(any("re-hook" in p for p in reel.retention_problems(sp)), "no mid-script re-hook is refused")
sp = copy.deepcopy(GOOD); sp["beats"] = sp["beats"][:2]
ok(any("every group" in p for p in reel.retention_problems(sp)), "every group needs an intent note")
sp = copy.deepcopy(GOOD); sp["beats"][2]["role"] = "escalation"
ok(any("reveal" in p for p in reel.retention_problems(sp)), "the reveal must be marked")

# 3. the contract: required for new reels, not for revisions of an approved script
prof = {"id": "flystar", "name": "Flying Star Aviator", "website": "https://www.flystar.co.in", "language": "hinglish"}
bare = {k: v for k, v in GOOD.items() if k not in ("hook_options", "beats")}
new_chk, rev_chk = reel.make_spec_check(reel.brandmod.LEGACY, FS), reel.make_spec_check(reel.brandmod.LEGACY, None)
ok(any("hook_options" in p for p in new_chk(copy.deepcopy(bare))), "a new reel without hooks and beats goes back to the writer")
ok(not any("hook_options" in p or "beats" in p for p in rev_chk(copy.deepcopy(bare))), "a revision of an approved reel is not forced to restructure")
sp = copy.deepcopy(GOOD); sp["groups"][2]["phrases"][0]["say"] = "Comment YES agar aap jaante the"
ok(any("engagement bait" in p for p in rev_chk(sp)), "engagement bait is refused in revisions too")

# 4. the writer's brief, the critic and the saved output
r = brain.prompt("retention")
ok(all(x in r for x in ["hook_options", "rehook", "Loss aversion", "Engagement bait", "One reel = one action"]), "the retention brief names hooks, re-hooks, levers and the bans")
c = brain.prompt("critic")
ok("`escalation`" in c and "`resonance`" in c and "escalation >= 6" in c and "caps `total` at 60" in c, "the Hook Critic scores escalation and resonance and caps engagement bait")
ok("escalation" in schemas.CRITIC_KEYS and "resonance" in schemas.CRITIC_KEYS and "hook_options" in schemas.SPEC["properties"], "schemas carry the new fields")
view = reel.critic_text(reel.brandmod.LEGACY, reel.validate_spec(copy.deepcopy(GOOD)), FS, "core")
ok('"hook_options"' in view and '"beats"' in view, "the Hook Critic sees the ranked hooks and intent notes")
ok(reel.hook_of(GOOD)["form"] == "open_statement", "the hook bank records the hook form for learning")

ok("Prefer Hindi words over English words" in reel.SAY_HI and "60-70" not in reel.SAY_HI, "Hinglish accounts (Flying Star) speak in We One Aviation's style: everyday Hindi, English only for technical words")

bad = [n for k, n in R if not k]; print(f"{len(R) - len(bad)}/{len(R)} retention checks passed" + (" | FAILED: " + "; ".join(bad) if bad else "")); sys.exit(1 if bad else 0)

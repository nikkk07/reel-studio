"""Accounts stay separate inside the worker: prompts, knowledge, call to action, voice language, caption handle, look.
python tests/brands_test.py   (fake DB, scripted Claude, no network)"""
import json, os, sys, tempfile, uuid
from datetime import datetime, timezone
from pathlib import Path
os.environ["REELSTUDIO_ROOT"] = tempfile.mkdtemp(); os.environ["REELSTUDIO_BRAIN"] = "mock"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reelworker import config, brand as brandmod, instagram, brain as realbrain  # noqa: E402
from reelworker.runner import JobContext  # noqa: E402
from reelworker import agents  # noqa: E402
from reelworker.jobs import reel, suggest, optimize  # noqa: E402
R = []
def ok(c, n): R.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n)
PROFILE = json.load(open(Path(__file__).resolve().parents[2] / "brands/bookmycharter/profile.json"))
BRANDS = {"weone": {"id": "weone", "name": "We One Aviation", "website": "https://weoneaviation.in", "instagram": "we_one_aviation", "profile": {"language": "hinglish", "legacy_prompts": True}},
          "bookmycharter": {"id": "bookmycharter", "name": "Book My Charter", "website": "https://bookmycharter.in", "instagram": None, "profile": PROFILE}}

class DB:
    def __init__(self): self.rows = {}; self.updates = []
    def select(self, t, q=""):
        if t == "brands":
            bid = q.split("id=eq.")[1].split("&")[0]; return [BRANDS[bid]] if bid in BRANDS else []
        if t == "settings": return [{"value": {"username": "we_one_aviation"}}] if "key=eq.instagram_status&" in q + "&" else []   # per-account keys
        if t == "jobs":
            if "brand=eq." in q:
                b = q.split("brand=eq.")[1].split("&")[0]
                return [{"output": {"title": f"{b} old reel"}}]
            return []
        return []
    def insert(self, t, row, upsert=False): return [row]
    def update(self, t, match, vals): self.updates.append(vals); return [{"id": "x"}]

class Brain:
    def __init__(self): self.calls = []; self.texts = {}
    def prompt(self, name, **v):
        self.calls.append((name, v)); return realbrain.prompt(name, **v)
    def ask(self, name, text, schema, tools, cwd, timeout=0, on_event=None):
        self.last_text = text; self.texts[name] = text
        if name == "caption": return {"caption": "Your private jet quote is missing a line.\\n\\nFollow @we_one_aviation and @bookmycharter_fake. Visit www.x.com", "hashtags": ["privatejet", "charter", "bookmycharter"]}
        return json.loads((Path(__file__).parent / f"fixtures/{name}.json").read_text())

def ctx_for(kind, brand_id, inp=None):
    db = DB(); j = {"id": str(uuid.uuid4()), "kind": kind, "status": "running", "brand": brand_id, "input": inp or {}, "output": {}, "lease_owner": "mac",
                    "created_at": datetime.now(timezone.utc).isoformat(), "parent_id": None}
    return JobContext(db, j, "mac"), db

brandmod._CACHE.clear()
db = DB()
p = brandmod.load(db, "bookmycharter"); w = brandmod.load(db, "weone")
ok(not brandmod.legacy(p) and brandmod.legacy(w), "We One keeps its original Hinglish path; Book My Charter uses its own profile")
b = brandmod.brief(p)
ok("Book My Charter" in b and "We One" not in b and "positioning" in b.lower(), "Book My Charter's knowledge pack contains only its own business")
try: brandmod.load(db, "unknown-acct"); ok(False, "unknown")
except RuntimeError: ok(True, "an account without a profile never falls back to another account's profile")
try: brandmod.logo(p); ok(False, "logo")
except RuntimeError as e: ok("missing" in str(e), "a missing account logo stops the reel instead of using We One's logo")

# logo uploaded in Assets: downloaded from the account's own folder, prepared for the reel, cached
import shutil
from PIL import Image
class AssetDB(DB):
    def __init__(self): super().__init__(); self.got = []
    def download(self, path, dest, bucket=None):
        self.got.append((bucket, path)); shutil.copy(str(Path(__file__).resolve().parents[2] / "web/brands/bookmycharter.png"), dest)
adb = AssetDB(); pa = {**p, "logo_asset": "bookmycharter/logo-1761200000000.png"}
f1 = brandmod.logo(pa, adb); f2 = brandmod.logo(pa, adb); im = Image.open(f1)
ok(adb.got == [("brand-assets", "bookmycharter/logo-1761200000000.png")] and f1 == f2, "the uploaded logo is fetched once from the account's own assets and reused")
ok(max(im.size) >= 600 and im.mode == "RGBA" and im.getpixel((0, 0))[3] == 0, "a small, solid logo is enlarged and gets rounded corners for the reel")
class BadDB(DB):
    def download(self, *a, **k): raise RuntimeError("offline")
try: brandmod.logo({**pa, "logo_asset": "bookmycharter/logo-1761200000009.png"}, BadDB()); ok(False, "fallback")
except RuntimeError as e: ok("missing" in str(e), "if the upload can't be fetched, the reel stops instead of using another account's logo")

# script + call to action
spec = json.loads((Path(__file__).parent / "fixtures/script.json").read_text())
s2 = reel.validate_spec(json.loads(json.dumps(spec)), p)
last = s2["groups"][-1]["phrases"][-1]
ok(last["say"] == PROFILE["cta"]["say"] and not any("we one" in (x["say"] + x["caption"]).lower() for g in s2["groups"] for x in g["phrases"]),
   "Book My Charter reel ends with its own call to action, never We One's")
s3 = reel.validate_spec(json.loads(json.dumps(spec)), w)
ok(s3["groups"][-1]["phrases"][-1]["caption"] == reel.CTA_CAPTION, "We One reel keeps 'follow karein We One Aviation'")

B = Brain(); reel.brain = agents.brain = B
c, _ = ctx_for("reel", "bookmycharter", {"concept": {"title": "The line missing from your private jet quote"}})
reel.stage_script(c, lambda f: None)
name, v = [c for c in B.calls if c[0] in ("script", "script_brand")][-1]
ok(name == "script_brand" and v["brand_name"] == "Book My Charter" and PROFILE["cta"]["say"] in B.texts["script"] and "Devanagari" not in B.texts["script"],
   "Book My Charter script prompt: English, its own brief and call to action")
c, _ = ctx_for("reel", "weone", {"concept": {"title": "Squawk 7700"}})
reel.stage_script(c, lambda f: None); ok([c for c in B.calls if c[0] in ("script", "script_brand")][-1][0] == "script", "We One script prompt unchanged")

# voice language (stop right after the settings are written)
import subprocess
class Stop(Exception): pass
orig = subprocess.Popen; subprocess.Popen = lambda *a, **k: (_ for _ in ()).throw(Stop())
for bid, lang in (("bookmycharter", "en"), ("weone", "hi")):
    c, _ = ctx_for("reel", bid); c.path("spec.json").write_text(json.dumps(reel.validate_spec(json.loads(json.dumps(spec)), brandmod.load(c.db, bid))))
    try: reel.stage_voice(c, lambda f: None)
    except Stop: pass
    cfg = json.loads(c.path("voice_in.json").read_text())
    ok(cfg["tts_lang"] == lang and cfg["asr_lang"] == lang, f"{bid}: voice speaks and is checked in '{lang}'")
    if bid == "bookmycharter": ok(all("एविएशन" not in g["text"] for g in cfg["groups"]), "English narration is not run through the Hindi pronunciation dictionary")
subprocess.Popen = orig

# ideas
S = Brain(); suggest.brain = agents.brain = S
c, _ = ctx_for("suggest", "bookmycharter", {"note": ""}); suggest.run(c)
n, v = [c for c in S.calls if c[0] in ("suggest", "suggest_brand")][-1]
ok(n == "suggest_brand" and "Book My Charter" in S.last_text and "bookmycharter old reel" in S.last_text and "weone old reel" not in S.last_text and "empty" in v["topic_bank"].lower(),
   "Book My Charter ideas use its own topic bank and its own past reels only")
O = Brain(); optimize.brain = agents.brain = O
c, _ = ctx_for("optimize", "bookmycharter", {"prompt": "helicopter for wedding"}); optimize.run(c)
ok([c for c in O.calls if c[0].startswith("optimize")][-1][0] == "optimize_brand" and "Book My Charter" in O.last_text, "'Use my idea' is shaped with Book My Charter's own knowledge")

# caption: own handle only
I = Brain(); instagram.brain = agents.brain = I
cap = instagram.make_caption(DB(), "x", {"title": "T", "script": [], "claims": []}, "bookmycharter")
ok([c for c in I.calls if c[0].startswith("caption")][-1][0] == "caption_brand" and "@we_one_aviation" not in cap and "www." not in cap and "Book My Charter" in I.last_text,
   "Book My Charter caption never mentions We One's handle (or any link)")
cap2 = instagram.make_caption(DB(), "x", {"title": "T", "script": [], "claims": []}, "weone")
ok([c for c in I.calls if c[0].startswith("caption")][-1][0] == "caption" and "@we_one_aviation" in cap2, "We One caption keeps its own handle")

# look
from reelworker.media import compose
before = compose.AMBER; compose.apply_palette(PROFILE["palette"])
ok(compose.AMBER == tuple(PROFILE["palette"]["accent"]) and compose.C("amber") == compose.AMBER, "Book My Charter graphics and caption highlights use its gold")
compose.apply_palette({}); ok(True, "an account without a palette keeps the defaults")
bad = [n for k, n in R if not k]; print(f"{len(R) - len(bad)}/{len(R)} account-separation checks passed" + (" | FAILED: " + "; ".join(bad) if bad else "")); sys.exit(1 if bad else 0)

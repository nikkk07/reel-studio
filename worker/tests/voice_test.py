"""Pronunciation: Pronunciation Editor -> voice takes -> Voice Inspector -> redo with better spellings -> hold if still wrong.
python tests/voice_test.py   (no voice model: takes and transcripts are simulated)"""
import json, os, sys, tempfile, uuid
from datetime import datetime, timezone
from pathlib import Path
os.environ["REELSTUDIO_ROOT"] = tempfile.mkdtemp(); os.environ["REELSTUDIO_BRAIN"] = "mock"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reelworker import agents, brain as realbrain  # noqa: E402
from reelworker import brand as brandmod  # noqa: E402
from reelworker.runner import JobContext  # noqa: E402
from reelworker.jobs import reel  # noqa: E402
R = []
def ok(c, n): R.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n)
FS = {"version": 1, "name": "Flying Star Aviator", "language": "hinglish", "tts_lang": "hi", "lexicon": [{"term": "DGCA", "say": "डी जी सी ए"}]}

class DB:
    def __init__(self): self.prof = json.loads(json.dumps(FS)); self.ins = []
    def select(self, t, q=""):
        if t == "brands": return [{"id": "flystar", "name": "Flying Star Aviator", "website": "w", "instagram": None, "logo_asset": None, "profile": self.prof}]
        return []
    def insert(self, t, row, upsert=False): self.ins.append((t, row)); return [row]
    def update(self, t, m, v):
        if t == "brands": self.prof = v["profile"]
        return [{"id": "x"}]

SPEC = {"title": "t", "voice": "female", "groups": [
    {"id": "g1", "phrases": [{"say": "एक email link — 24 घंटे में click नहीं किया,", "caption": "Ek email link"}]},
    {"id": "g2", "phrases": [{"say": "Pariksha पर registration form submit करो।", "caption": "Pariksha par registration"}, {"say": "follow करें Flying Star Aviator।", "caption": "follow"}]}],
    "shots": [], "graphics": [], "claims": []}

class Brain:
    def __init__(self, latin_first=False, inspector=None):
        self.asks = []; self.latin_first = latin_first; self.ins = list(inspector or []); self.n = 0
    def prompt(self, name, **v): return realbrain.prompt(name, **v)
    def ask(self, name, text, schema, tools, cwd, timeout=0, on_event=None):
        self.asks.append((name, text))
        if name == "pronounce":
            self.n += 1
            g1 = "एक ईमेल लिंक, ट्वेंटी फ़ोर घंटे में क्लिक नहीं किया," if not (self.latin_first and self.n == 1) else "एक email लिंक, 24 घंटे में click नहीं किया,"
            return {"phrases": [{"group": "g1", "index": 0, "tts": g1}, {"group": "g2", "index": 0, "tts": "परीक्षा पर रजिस्ट्रेशन फ़ॉर्म सबमिट करो।"},
                                {"group": "g2", "index": 1, "tts": "फ़ॉलो करें फ़्लाइंग स्टार एविएटर।"}],
                    "terms": [{"term": "registration", "say": "रजिस्ट्रेशन"}, {"term": "Flying Star Aviator", "say": "फ़्लाइंग स्टार एविएटर"}]}
        if name == "pron_check":
            parts = json.loads(text.split("Parts (id, the intended text, then each take with its id and transcript):\n", 1)[1].split("\n\nFor every part", 1)[0])
            verdict = self.ins.pop(0) if self.ins else {}
            out = []
            for p in parts:
                v = verdict.get(p["id"], {"ok": True})
                best = v.get("best") or p["takes"][-1]["take"]
                out.append({"id": p["id"], "best_take": best, "ok": v["ok"], "problems": v.get("problems", []), "retry_text": ""})
            return {"parts": out}
        raise AssertionError(name)

RUNS = []
def fake_run(ctx, cfg, cfg_name, out_name, prog, lo, hi, label):
    RUNS.append((cfg_name, [g["id"] for g in cfg["groups"]], cfg["seeds"], [g["text"] for g in cfg["groups"]], cfg.get("temperature")))
    out = {}
    for g in cfg["groups"]:
        takes = [{"file": str(ctx.dir / "vo" / f"{g['id']}_s{s}.wav"), "score": 0.5, "word_prob": 0.8, "coverage": 1.0, "utmos": 4.2,
                  "transcript": g["text"], "weak_words": [], "text": g["text"], "trim": [0, 1], "spans": [[0, 1]] * len(g["phrases_say"])} for s in cfg["seeds"]]
        out[g["id"]] = {"file": takes[0]["file"], "trim": [0, 1], "spans": takes[0]["spans"], "chosen": takes[0], "takes": takes}
    ctx.path(out_name).write_text(json.dumps(out, ensure_ascii=False))
    return out
reel._run_voice = fake_run
reel._lexicon_entries = lambda: [{"match": ["CPL"], "say": "सी पी एल"}]

def ctx_for(db):
    j = {"id": str(uuid.uuid4()), "kind": "reel", "status": "running", "brand": "flystar", "input": {"concept": {"title": "x"}}, "output": {}, "lease_owner": "mac",
         "created_at": datetime.now(timezone.utc).isoformat(), "parent_id": None}
    c = JobContext(db, j, "mac"); c.merge_output = lambda v: c.job["output"].update(v)
    c.path("spec.json").write_text(json.dumps(SPEC, ensure_ascii=False)); return c

# 1. clean run: editor makes Devanagari-only text; inspector approves first time
brandmod._CACHE.clear(); agents._CACHE.clear(); RUNS.clear()
db = DB(); b = Brain(); reel.brain = agents.brain = b; c = ctx_for(db); reel.stage_voice(c, lambda f: None)
ok(RUNS[0][3][0] == "एक ईमेल लिंक, ट्वेंटी फ़ोर घंटे में क्लिक नहीं किया," and len(RUNS) == 1, "the voice model reads the Pronunciation Editor's spoken form (no Latin, no digits, no dashes)")
ok("Rules for `tts`" in b.asks[0][1] and "डी जी सी ए" in b.asks[0][1] and "सी पी एल" in b.asks[0][1], "the editor gets the rules and both dictionaries (studio + this account's)")
ok(SPEC["groups"][0]["phrases"][0]["caption"] == "Ek email link", "captions are never changed")
ok(not c.job["output"].get("hold") and c.job["output"]["voice_issues"] == {}, "all words right -> no hold")
ok({t["term"] for t in db.prof["lexicon"]} >= {"DGCA", "registration", "Flying Star Aviator"}, "new spellings are saved in the account's own dictionary for next time")

# 2. the Supervisor rejects Latin letters / digits left in the spoken form
brandmod._CACHE.clear(); RUNS.clear(); db = DB(); b = Brain(latin_first=True); reel.brain = agents.brain = b; c = ctx_for(db); reel.stage_voice(c, lambda f: None)
ok([a[0] for a in b.asks].count("pronounce") == 2 and "Latin letters left" in b.asks[1][1] and "digits left" in b.asks[1][1], "Latin letters or digits left for the voice -> sent back with the exact lines")

# 3. a mispronounced word: the part is recorded again with the inspector's better spelling; the right take wins
brandmod._CACHE.clear(); RUNS.clear(); db = DB()
bad = {"g2": {"ok": False, "best": "g2_s11", "problems": [{"intended": "रजिस्ट्रेशन", "heard": "लेजिस्ट्रेश्ट्रेश्ण", "fix_say": "रजिस्ट्रेशन्"}]}}
b = Brain(inspector=[bad, {"g2": {"ok": True, "best": "g2_s83"}}]); reel.brain = agents.brain = b; c = ctx_for(db); reel.stage_voice(c, lambda f: None)
ok(len(RUNS) == 2 and RUNS[1][1] == ["g2"] and RUNS[1][2] == [71, 83, 97, 101, 113] and "रजिस्ट्रेशन्" in RUNS[1][3][0], "only the wrong part is recorded again, with new seeds and the better spelling")
vo = json.loads(c.path("voice_out.json").read_text())
ok(vo["g2"]["file"].endswith("g2_s83.wav") and len(vo["g2"]["takes"]) == 8, "the Voice Inspector's choice (said right) becomes the narration")
ok(not c.job["output"].get("hold"), "fixed on the retry -> no hold")
ok(RUNS[0][4] is None and RUNS[1][4] == 0.65, "retakes are recorded with a steadier delivery (lower temperature)")
ok(any(r["agent"] == "voice_inspector" for t, r in db.ins if t == "agent_runs"), "the Voice Inspector's checks are logged for the learning loop")

# 4. still wrong after two retries -> held for a person, never auto-posted
brandmod._CACHE.clear(); RUNS.clear(); db = DB()
b = Brain(inspector=[bad, bad, bad, bad]); reel.brain = agents.brain = b; c = ctx_for(db); reel.stage_voice(c, lambda f: None)
ok(len(RUNS) == 4 and c.job["output"]["hold"] is True and "लेजिस्ट्रेश्ट्रेश्ण" in c.job["output"]["hold_reason"], "still mispronounced after 3 retries -> held, with the word named")

bad_ = [n for k, n in R if not k]; print(f"{len(R) - len(bad_)}/{len(R)} pronunciation checks passed" + (" | FAILED: " + "; ".join(bad_) if bad_ else "")); sys.exit(1 if bad_ else 0)

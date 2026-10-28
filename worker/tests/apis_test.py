"""Groq backup brain + Whisper, Coverr footage, ThumbAPI thumbnails - all with stand-in HTTP, no real calls.
    python tests/apis_test.py"""
import base64, io, json, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from reelworker import groq, brain, studio, thumbs, config      # noqa: E402
from reelworker.media import footage                              # noqa: E402
from reelworker.runner import QuotaError                          # noqa: E402

R = []
def ok(c, n): R.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n)
class Resp:
    def __init__(self, status=200, body=None, text=None): self.status_code = status; self._b = body; self.text = text if text is not None else json.dumps(body or {})
    def json(self): return self._b if self._b is not None else json.loads(self.text)

KEYS = {}
studio.get = lambda name: KEYS.get(name)
studio.thumbs_on = lambda: KEYS.get("_thumbs", False)
SCHEMA = {"type": "object", "required": ["caption", "hashtags"], "properties": {"caption": {"type": "string"}, "hashtags": {"type": "array", "minItems": 3, "items": {"type": "string"}}}}

# 1. Groq: only with a key, only when it fits the free tier, answers checked against the schema (one fix round), other model on 429
ok(not groq.fits("x" * 30000, SCHEMA, 2500) and groq.fits("x" * 3000, SCHEMA, 1500), "free-tier size guard (8,000 tokens a minute)")
try: groq.chat("caption", "hi", SCHEMA); ok(False, "no key")
except groq.GroqUnavailable as e: ok("no Groq key" in str(e), "without a key Groq is never called")
KEYS["GROQ_API_KEY"] = "gsk_test"; calls = []
answers = [Resp(429, {}), Resp(200, {"choices": [{"message": {"content": '{"caption": "Hi", "hashtags": ["a"]}'}}]}),
           Resp(200, {"choices": [{"message": {"content": '```json\n{"caption": "Hi", "hashtags": ["a","b","c"]}\n```'}}]})]
def fake_post(url, **kw): calls.append((url, kw.get("json", {}).get("model"))); return answers.pop(0)
httpx.post = fake_post
obj = groq.chat("caption", "Write a caption", SCHEMA, out_tokens=1500)
ok(obj["hashtags"] == ["a", "b", "c"] and [c[1] for c in calls] == ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "openai/gpt-oss-20b"],
   "limit on one model -> the other; a schema miss is sent back once and fixed")

# 2. brain.ask: Claude limited -> Groq answers text-only steps; steps that need web or images still wait for Claude
def limited(*a, **k): raise QuotaError("You've hit your limit · resets 5pm", "2026-10-28T12:00:00Z")
brain._ask_claude = limited
config.BRAIN_MODE = "claude"
httpx.post = lambda url, **kw: Resp(200, {"choices": [{"message": {"content": '{"caption": "Backup", "hashtags": ["x","y","z"]}'}}]})
out = brain.ask("caption", "Write a caption", SCHEMA, [], Path(tempfile.mkdtemp()))
ok(out["caption"] == "Backup" and brain.LAST["brain"] == "groq", "Claude limited: caption written by Groq")
try: brain.ask("script", "Write a script", SCHEMA, ["WebSearch"], Path(tempfile.mkdtemp())); ok(False, "web step")
except QuotaError: ok(True, "a step that needs web research still waits for Claude")
KEYS.pop("GROQ_API_KEY")
try: brain.ask("caption", "Write a caption", SCHEMA, [], Path(tempfile.mkdtemp())); ok(False, "no key")
except QuotaError: ok(True, "no Groq key: the job waits for Claude exactly as before")
def other(*a, **k): raise RuntimeError("Claude call failed: some bug")
brain._ask_claude = other; KEYS["GROQ_API_KEY"] = "gsk_test"
try: brain.ask("caption", "x", SCHEMA, [], Path(tempfile.mkdtemp())); ok(False, "bug")
except RuntimeError: ok(True, "a real Claude error is not hidden by the backup")

# 3. Whisper on Groq: transcript, or '' on any problem
w = Path(tempfile.mkdtemp()) / "a.wav"; w.write_bytes(b"RIFF")
httpx.post = lambda url, **kw: Resp(200, {"text": " नमस्ते पायलट "}) if url.endswith("/audio/transcriptions") and kw["data"]["model"] == "whisper-large-v3" else Resp(500, {})
ok(groq.transcribe(w, "hi") == "नमस्ते पायलट", "Whisper large-v3 transcript")
httpx.post = lambda url, **kw: Resp(429, {})
ok(groq.transcribe(w, "hi") == "", "Whisper limit: empty, never an error")

# 4. Coverr: a third source, mixed in so the 12-clip cap keeps every library; 429 pauses it for 15 minutes
KEYS.update(PEXELS_API_KEY="p", PIXABAY_API_KEY="1-x", COVERR_API_KEY="c" * 20)
def fake_get(url, params=None, headers=None, **kw):
    if "pexels" in url: return Resp(200, {"videos": [{"id": i, "url": "u", "duration": 9, "image": "t", "user": {"name": "P"},
                                                      "video_files": [{"width": 1080, "height": 1920, "link": f"pex{i}"}]} for i in range(6)]})
    if "pixabay" in url: return Resp(200, {"hits": [{"id": i, "pageURL": "u", "duration": 9, "user": "X", "videos": {"large": {"url": f"pix{i}", "width": 1920, "height": 1080, "thumbnail": "t"}, "medium": {}}} for i in range(6)]})
    if "coverr" in url:
        assert headers["Authorization"] == "Bearer " + "c" * 20 and params["urls"] == "true"
        return Resp(200, {"hits": [{"id": f"cv{i}", "duration": 12.5, "max_width": 3840, "max_height": 2160, "thumbnail": "t",
                                    "urls": {"mp4_download": f"cov{i}"}} for i in range(6)]})
footage.httpx.get = fake_get
c = footage.search("airplane cockpit", per_source=6)[:12]
srcs = [x["src"] for x in c]
ok(srcs.count("coverr") == 4 and srcs.count("pexels") == 4 and srcs.count("pixabay") == 4, f"12 clips = 4 from each library ({srcs[:6]}…)")
cv = next(x for x in c if x["src"] == "coverr")
ok(cv["dl"] == "cov0" and cv["licence"] == "Coverr License" and cv["w"] == 3840, "Coverr clip: download link (counts the download), licence, size")
footage.httpx.get = lambda url, **kw: Resp(429, {}) if "coverr" in url else fake_get(url, **kw)
footage.search("x"); ok(footage._coverr_paused(), "Coverr hourly limit: paused for 15 minutes, other libraries continue")

# 5. ThumbAPI: off unless switched on AND the account has YouTube; JPEG 1280x720 stored next to the cover
class DB:
    def __init__(self, yt): self.yt = yt; self.up = []
    def select(self, t, q): return [{"value": {"state": "connected"}}] if self.yt and "youtube_status@flystar" in q else []
    def upload(self, path, f, ct): self.up.append((path, ct)); return path
from PIL import Image
buf = io.BytesIO(); Image.new("RGB", (1280, 720), (10, 20, 30)).save(buf, "WEBP")
img = "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()
sent = []
httpx.post = lambda url, **kw: (sent.append(kw["json"]), Resp(200, {"image": img}))[1]
KEYS["THUMBAPI_KEY"] = "t" * 20; d = Path(tempfile.mkdtemp())
ok(thumbs.make(DB(True), "flystar", "j1", "Why pilots say Roger", d) is None and not sent, "switched off: no ThumbAPI call (it costs credits)")
KEYS["_thumbs"] = True
ok(thumbs.make(DB(False), "flystar", "j1", "T", d) is None and not sent, "account without YouTube: no call")
db = DB(True); p = thumbs.make(db, "flystar", "j1", "Why pilots say Roger", d)
ok(p == "j1/yt_thumb.jpg" and db.up == [("j1/yt_thumb.jpg", "image/jpeg")] and Image.open(d / "yt_thumb.jpg").size == (1280, 720) and sent[0]["format"] == "youtube",
   "on + YouTube connected: one thumbnail, saved as a 1280x720 JPEG")
httpx.post = lambda url, **kw: Resp(402, text="out of credits")
logs = []; ok(thumbs.make(DB(True), "flystar", "j2", "T", d, log=logs.append) is None and "402" in logs[0], "out of credits: skipped with a note, the reel is unaffected")

bad = [n for k, n in R if not k]; print(f"{len(R) - len(bad)}/{len(R)} API checks passed" + (" | FAILED: " + "; ".join(bad) if bad else "")); sys.exit(1 if bad else 0)

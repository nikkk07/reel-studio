"""Finished ad videos -> CRM > Content > Ad creatives (crm.push_ads). Fake DB and CRM, no network; needs ffmpeg.
python tests/crm_ads_test.py"""
import json, os, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(tempfile.mkdtemp(prefix="crmads_"))
os.environ["REELSTUDIO_JOBS_DIR"] = str(ROOT / "jobs")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reelworker import config, crm  # noqa: E402

config.APP = ROOT
R = []
def ok(c, n): R.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n, flush=True)


def job(jid, status="review", **inp):
    d = config.JOBS_DIR / jid / "render"; d.mkdir(parents=True, exist_ok=True)
    mp4 = d / "reel.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=navy:s=540x960:d=2", "-pix_fmt", "yuv420p", str(mp4)], check=True)
    (d / "result.json").write_text(json.dumps({"delivery": str(mp4), "thumb": str(d / "thumb.jpg"), "duration": 27.4}))
    (config.JOBS_DIR / jid / "spec.json").write_text(json.dumps({"title": "Pilot after 12th: check this first", "groups": [
        {"phrases": [{"say": "12th ke baad pilot banna hai?"}, {"say": "Fees bharne se pehle, ye ek cheez check karo."}]}]}))
    return {"id": jid, "status": status, "input": {"ad": True, **inp}, "output": {}}


class DB:
    def __init__(self, jobs): self.jobs = jobs; self.q = []
    def select(self, t, q=""):
        self.q.append(q)
        return [j for j in self.jobs if j["input"].get("ad") and j["status"] in ("review", "done")] if t == "jobs" else []


sent, puts = [], []
def fake_call(action, **b):
    sent.append((action, b))
    if action == "adopt": return {"ok": True, "id": 41}
    if action == "video_upload":
        return {"video": {"path": f"ad/{b['id']}/video.mp4", "url": f"https://crm/upload/sign/content/ad/{b['id']}/video.mp4?token=v"},
                "thumb": {"path": f"ad/{b['id']}/thumb.jpg", "url": f"https://crm/upload/sign/content/ad/{b['id']}/thumb.jpg?token=t"}}
    return {"ok": True}
class Resp:
    status_code = 200; text = ""
def fake_put(url, content=b"", headers=None, timeout=0):
    puts.append((url, len(content), (headers or {}).get("content-type"))); return Resp()

crm.call, crm.httpx.put = fake_call, fake_put
db = DB([job("6fdf67c1-aaaa"), job("b2-ordered", crm_item=17)])
db.jobs.append({"id": "c3-norender", "status": "review", "input": {"ad": True}, "output": {}})
n = crm.push_ads(db)
ok(n == 2 and "input->>ad=eq.true" in db.q[0] and "status=in.(review,done)" in db.q[0], "AD01 both finished ad videos are sent; one still rendering elsewhere is left for later")
ad = [b for a, b in sent if a == "adopt"]
ok(len(ad) == 1 and ad[0]["studio_job"] == "6fdf67c1-aaaa" and ad[0]["title"] == "Pilot after 12th: check this first"
   and ad[0]["script"].startswith("12th ke baad") and "Fees bharne" in ad[0]["summary"], "AD02 an ad made here becomes a CRM item with its title and script; a CRM-ordered one keeps its item")
ok([u for u, _, _ in puts] == ["https://crm/upload/sign/content/ad/41/video.mp4?token=v", "https://crm/upload/sign/content/ad/41/thumb.jpg?token=t",
                               "https://crm/upload/sign/content/ad/17/video.mp4?token=v", "https://crm/upload/sign/content/ad/17/thumb.jpg?token=t"]
   and [c for _, _, c in puts] == ["video/mp4", "image/jpeg"] * 2 and all(s > 0 for _, s, _ in puts), "AD03 the mp4 and a cover frame go straight to storage by the one-time links")
cov = config.JOBS_DIR / "6fdf67c1-aaaa" / "render" / "cover.jpg"
w = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(cov)], capture_output=True, text=True).stdout.strip()
ok(w == "1080,1920", "AD04 the cover is a full-size 1080x1920 frame from the hook")
ok([b for a, b in sent if a == "video_done"] == [{"id": 41, "seconds": 27.4}, {"id": 17, "seconds": 27.4}], "AD05 the CRM is told the video is ready, with its length")
sent.clear(); puts.clear()
ok(crm.push_ads(db) == 0 and not sent, "AD06 nothing is sent twice")

# a failed upload is retried next round and does not block the others
st = json.loads((ROOT / "state" / "crm_videos.json").read_text()); st.pop("b2-ordered"); (ROOT / "state" / "crm_videos.json").write_text(json.dumps(st))
db.jobs.append(job("d4-new"))
class Bad(Resp): status_code = 500; text = "boom"
calls = {"n": 0}
def flaky(url, content=b"", headers=None, timeout=0):
    calls["n"] += 1
    return Bad() if "/17/" in url else Resp()
crm.httpx.put = flaky
ok(crm.push_ads(db) == 1 and json.loads((ROOT / "state" / "crm_videos.json").read_text()).get("d4-new", {}).get("done")
   and not json.loads((ROOT / "state" / "crm_videos.json").read_text()).get("b2-ordered", {}).get("done"), "AD07 a failed upload is tried again later and does not stop the next ad")
big = job("e5-big"); Path(json.loads((config.JOBS_DIR / "e5-big/render/result.json").read_text())["delivery"]).write_bytes(b"\0" * int(49e6))
db.jobs = [big]; sent.clear()
ok(crm.push_ads(db) == 0 and not sent, "AD08 a video over 48 MB is not sent (the CRM bucket takes 50 MB)")
rep = crm.report_of({"kind": "reel", "status": "review", "input": {"ad": True, "crm_item": 17}, "output": {}})
ok(rep == {"status": "ready", "media": {"video": crm.STUDIO}}, "AD09 the status report no longer says to download it by hand")

print(f"\n{sum(c for c, _ in R)}/{len(R)} passed"); sys.exit(0 if all(c for c, _ in R) else 1)

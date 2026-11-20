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

# ---------------------------------------------------------------- CRM > Ad studio asks this Mac to post (CRM 120)
class PDB:
    """jobs with a PostgREST-like guarded PATCH: only rows whose Instagram state is still open are changed."""
    def __init__(self, jobs): self.jobs = {j["id"]: j for j in jobs}; self.patches = []
    def select(self, t, q=""):
        if "id=eq." in q:
            j = self.jobs.get(q.split("id=eq.")[1].split("&")[0]); return [j] if j else []
        if "output->ig->>via=eq.crm" in q:
            return [j for j in self.jobs.values() if ((j.get("output") or {}).get("ig") or {}).get("via") == "crm"]
        return []
    def update(self, t, m, v):
        j = self.jobs.get(m.split("id=eq.")[1].split("&")[0]); self.patches.append((m, v))
        st = ((j or {}).get("output") or {}).get("ig", {}).get("state")
        if not j or ("or=(" in m and st not in (None, *crm.OPEN_IG)): return []
        j.update(v); return [j]

base = {"ig_video": "6f/ig.mp4", "thumb": "6f/thumb.jpg", "ig_caption": "Claude's caption", "autopilot_hold": True}
pdb = PDB([{"id": "6f", "status": "review", "input": {"ad": True}, "output": dict(base)},
           {"id": "96", "status": "done", "input": {"ad": True}, "output": {**base, "ig": {"state": "posting", "via": "crm", "crm_item": 2}}},
           {"id": "nf", "status": "review", "input": {"ad": True}, "output": {}},
           {"id": "rl", "status": "done", "input": {}, "output": dict(base)}])
acks = []
def pcall(action, **b):
    acks.append((action, b))
    if action == "post_tasks":
        return {"tasks": [{"id": 1, "action": "schedule", "studio_job": "6f", "post_at": "2026-11-21T07:30:00Z", "ai": True, "sig": "a" * 32,
                           "caption": "12th ke baad pilot? Free career counselling. #pilottraining"},
                          {"id": 2, "action": "cancel", "studio_job": "96"},
                          {"id": 3, "action": "schedule", "studio_job": "nf", "post_at": "2026-11-21T07:30:00Z", "caption": "long enough caption", "sig": "b" * 32},
                          {"id": 4, "action": "schedule", "studio_job": "rl", "post_at": "2026-11-21T07:30:00Z", "caption": "long enough caption", "sig": "c" * 32}]}
    return {"ok": True}
crm.call = pcall
ok(crm.post_tasks(pdb) == 4, "AD10 every posting instruction from the CRM gets an answer")
o = pdb.jobs["6f"]["output"]; a = {b["id"]: b for x, b in acks if x == "post_ack"}
ok(pdb.jobs["6f"]["status"] == "done" and o["ig"] == {"state": "scheduled", "at": "2026-11-21T07:30:00Z", "via": "crm", "crm_item": 1, "attempts": 0}
   and o["ig_caption"].startswith("12th ke baad") and o["ig_ai"] is True and o["autopilot_hold"] is False and o["thumb"] == "6f/thumb.jpg"
   and a[1] == {"id": 1, "sig": "a" * 32, "state": "scheduled", "note": None},
   "AD11 scheduled here with the CRM's time, caption and AI label; the CRM is told which instruction was carried out")
ok(a[2]["state"] == "too_late" and pdb.jobs["96"]["output"]["ig"]["state"] == "posting", "AD12 a post already under way is never cancelled")
ok(a[3]["state"] == "failed" and "storage" in a[3]["note"] and a[4]["state"] == "failed" and pdb.jobs["rl"]["status"] == "done"
   and "ig" not in pdb.jobs["rl"]["output"], "AD13 a missing file or a job that is not an ad video is refused with a reason (an ordinary reel is never touched)")
acks.clear()
def ccall(action, **b):
    acks.append((action, b))
    return {"tasks": [{"id": 1, "action": "cancel", "studio_job": "6f"}]} if action == "post_tasks" else {"ok": True}
crm.call = ccall
crm.post_tasks(pdb)
ok(pdb.jobs["6f"]["status"] == "review" and pdb.jobs["6f"]["output"]["ig"]["state"] == "cancelled"
   and [b["state"] for x, b in acks if x == "post_ack"] == ["cancelled"], "AD14 unscheduled in the CRM: the post is stopped here and the video goes back to review")
done = {"id": "6f", "kind": "reel", "status": "done", "input": {"ad": True},
        "output": {"ig": {"state": "posted", "via": "crm", "crm_item": 1, "media_id": "17960000000000002", "permalink": "https://www.instagram.com/reel/x/",
                          "posted_at": "2026-11-21T07:31:00Z"}, "ig_stats": {"views": 950, "likes": 61, "saved": 12}}}
ok(crm.report_of(done) == {"status": "posted", "posted_at": "2026-11-21T07:31:00Z", "stats": {"views": 950, "likes": 61, "saves": 12},
                           "ig_media_id": "17960000000000002", "media": {"permalink": "https://www.instagram.com/reel/x/"}},
   "AD15 once posted, the CRM gets the post id, link and numbers (for Use in ads)")
fail = {**done, "output": {"ig": {"state": "failed", "via": "crm", "crm_item": 1, "error": "Posting failed: video too short"}}}
ok(crm.report_of(fail) == {"status": "ready", "ig_error": "Posting failed: video too short"}
   and crm.report_of({**done, "output": {"ig": {"state": "scheduled", "via": "crm", "crm_item": 1}}}) is None,
   "AD16 a refused post goes back to the owner with the reason; while it waits nothing is re-reported")
pdb.jobs["6f"].update(done); sent_up = []
crm.call = lambda action, **b: sent_up.append((action, b)) or {"ok": True}
(ROOT / "state" / "crm_sent.json").write_text("{}")
class SDB(PDB):
    def select(self, t, q=""):
        if "crm_item=not.is.null" in q: return []
        return super().select(t, q)
sdb = SDB([pdb.jobs["6f"]])
ok(crm.sync(sdb) == 1 and sent_up[0][1]["id"] == 1 and sent_up[0][1]["ig_media_id"] == "17960000000000002",
   "AD17 an ad made here and adopted by the CRM reports its post under the CRM item from the instruction")

print(f"\n{sum(c for c, _ in R)}/{len(R)} passed"); sys.exit(0 if all(c for c, _ in R) else 1)

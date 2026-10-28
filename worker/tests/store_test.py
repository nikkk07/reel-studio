"""Storage: R2 when it is set up and checked, Supabase otherwise; one posting copy; the Mac keeps only what is still needed.
    python tests/store_test.py"""
import datetime, os, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reelworker import store, config  # noqa: E402

R = []
def ok(c, n): R.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n)

# 1. the signer matches AWS's published Signature V4 example
r = store.R2("x", "examplebucket", "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", host="examplebucket.s3.amazonaws.com")
u = r.presign("test.txt", 86400, now=datetime.datetime(2013, 5, 24, tzinfo=datetime.timezone.utc), uri="/test.txt", region="us-east-1")
ok(u.endswith("X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404"), "R2 links signed like AWS's example")
ok("X-Amz-Expires=604800" in store.R2("a" * 32, "b", "k", "s").presign("p/ig.mp4", 30 * 86400), "links never last longer than 7 days (the S3 maximum)")

class DB:
    def __init__(self, r2=None): self.up = []; self.r2 = r2
    def select(self, t, q=""):
        if t == "settings" and "key=eq.r2" in q: return [{"value": self.r2}] if self.r2 else []
        if t == "secrets": return [{"key": "r2_access_key_id", "value": "a" * 32}, {"key": "r2_secret_access_key", "value": "b" * 64}]
        return []
    def upload(self, path, f, ct): self.up.append(path); return path

tmp = Path(tempfile.mkdtemp()); v = tmp / "ig.mp4"; v.write_bytes(b"x" * 1000); th = tmp / "t.jpg"; th.write_bytes(b"j")
# 2. no R2 (or not checked yet): Supabase, one video copy + thumbnail
db = DB(); out = store.put_reel(db, "job1", v, th)
ok(out["store"] == "sb" and db.up == ["job1/ig.mp4", "job1/thumb.jpg"] and out["video"] == out["ig_video"] == "job1/ig.mp4", "without R2: one copy in Supabase (no second reel.mp4)")
db = DB({"state": "checking", "account_id": "a" * 32, "bucket": "b"}); out = store.put_reel(db, "job2", v, th)
ok(out["store"] == "sb", "R2 saved but not yet confirmed by the cloud: Supabase is used")
# 3. R2 checked: the video goes to R2, the thumbnail stays in Supabase, a 7-day playable link is stored
put = []
store.R2.put = lambda self, key, f, ct: put.append(key) or key
db = DB({"state": "ok", "account_id": "a" * 32, "bucket": "reels"}); out = store.put_reel(db, "job3", v, th)
ok(out["store"] == "r2" and put == ["job3/ig.mp4"] and db.up == ["job3/thumb.jpg"] and out["play_url"].startswith(f"https://{'a' * 32}.r2.cloudflarestorage.com/reels/job3/ig.mp4?"),
   "with R2: video in R2, thumbnail in Supabase, playable link saved")
# 4. R2 failing never loses the reel
def boom(self, key, f, ct): raise RuntimeError("R2 PUT failed (HTTP 403)")
store.R2.put = boom; logs = []
db = DB({"state": "ok", "account_id": "a" * 32, "bucket": "reels"}); out = store.put_reel(db, "job4", v, th, log=logs.append)
ok(out["store"] == "sb" and "job4/ig.mp4" in db.up and logs, "R2 down: the reel goes to Supabase instead, with a warning")

# 5. the Mac's cleanup: work folders of removed/stopped reels and masters older than 3 days go; running and recent ones stay
from reelworker import worker  # noqa: E402
jobs = tmp / "jobs"; deliv = tmp / "deliverables"; (deliv / "2026-10-20").mkdir(parents=True); jobs.mkdir()
ids = {k: f"{k:0<8}-0000-0000-0000-000000000000"[:36] for k in ("pruned", "stopped", "fresh", "running", "review")}
for k, i in ids.items(): (jobs / i).mkdir(); (jobs / i / "x").write_text("1")
old = deliv / "2026-10-20" / "old_master.mp4"; old.write_bytes(b"m"); os.utime(old, (time.time() - 4 * 86400,) * 2)
new = deliv / "2026-10-20" / "new_master.mp4"; new.write_bytes(b"m")
config.JOBS_DIR, config.DELIVERABLES = jobs, deliv
worker.CURRENT.clear(); worker.CURRENT["laneB"] = {"id": ids["running"]}
day_ago = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
rows = [{"id": ids["pruned"], "status": "done", "updated_at": day_ago, "pruned": "true"}, {"id": ids["stopped"], "status": "cancelled", "updated_at": day_ago, "pruned": None},
        {"id": ids["fresh"], "status": "done", "updated_at": day_ago, "pruned": None}, {"id": ids["review"], "status": "review", "updated_at": day_ago, "pruned": None}]
class PDB:
    def delete(self, *a): pass
    def select(self, t, q=""): return rows if t == "jobs" else []
worker.prune(PDB())
left = {k for k, i in ids.items() if (jobs / i).exists()}
ok(left == {"fresh", "running", "review"}, f"work folders: removed reel and stopped reel deleted; posted-recently, running and waiting kept ({sorted(left)})")
ok(not old.exists() and new.exists(), "masters older than 3 days deleted; newer ones kept")

bad = [n for k, n in R if not k]; print(f"{len(R) - len(bad)}/{len(R)} storage checks passed" + (" | FAILED: " + "; ".join(bad) if bad else "")); sys.exit(1 if bad else 0)

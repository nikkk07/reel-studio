"""make_local.py: one reel without Supabase - the job row, uploads and finish all stay on the Mac."""
import os, sys, tempfile, json
from pathlib import Path
tmp = tempfile.mkdtemp()
os.environ["REELSTUDIO_JOBS_DIR"] = tmp + "/jobs"; os.environ["REELSTUDIO_DELIVERABLES"] = tmp + "/deliv"
sys.path.insert(0, str(Path(__file__).resolve().parents[1])); sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import make_local  # noqa: E402
from reelworker import config, store  # noqa: E402
from reelworker.runner import JobContext  # noqa: E402
R = []
def ok(c, n): R.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n)
make_local.STORE = Path(tmp) / "store"
db = make_local.LocalDB(Path(tmp) / "jobs" / "j1" / "local_job.json")
db.insert("jobs", {"id": "j1", "kind": "reel", "status": "running", "lease_owner": "w", "input": {"ad": True}})
ctx = JobContext(db, db.job, "w")
ctx.merge_output({"title": "T"}); ctx.progress("voice", 0.5); ctx.log("hello"); ctx.trace("x", "y", "z", flush=True)
v = Path(tmp) / "a.mp4"; v.write_bytes(b"0" * 10); t = Path(tmp) / "t.jpg"; t.write_bytes(b"1")
out = store.put_reel(db, "j1", v, t)
ok(out["store"] == "sb" and (make_local.STORE / "j1" / "ig.mp4").exists(), "LM01 the finished reel is kept on the Mac (no cloud)")
ctx.finish("review")
ok(json.loads((Path(tmp) / "jobs" / "j1" / "local_job.json").read_text())["status"] == "review", "LM02 the job finishes in review, stored locally")
ok(db.select("agent_lessons", "select=*") == [] and db.select("jobs", "select=status&id=eq.j1") == [{"status": "review"}], "LM03 other reads are empty; the job reads back")
print(f"\n{sum(c for c, _ in R)}/{len(R)} passed"); sys.exit(0 if all(c for c, _ in R) else 1)

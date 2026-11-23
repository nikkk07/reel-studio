"""Make one reel from an approved spec.json entirely on this Mac, without Supabase (for when the cloud project is down or
restricted). Same stages, voice, AI presenter (Kaggle) and editing as the normal worker; nothing is read from or written
to the website. The job lives in ~/ReelStudio/jobs/<id>/ as usual; the finished video is copied to
~/ReelStudio/deliverables/local/<title>_<id8>.mp4 and nothing is ever posted.

    ~/ReelStudio/.venv/bin/python worker/scripts/make_local.py ad_spec.json --video presenter_studio --ad
    ... --job <id>      resume the same job after a stop (finished stages are kept)

API keys (Pexels, Pixabay, ...) come from the Mac's own .env, as the worker already does when the site is unreachable.
"""
import argparse, json, logging, os, re, shutil, sys, uuid
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("REELSTUDIO_WORKER_ID", "local-" + (os.uname().nodename.split(".")[0] or "mac"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reelworker import config  # noqa: E402
from reelworker.runner import JobContext  # noqa: E402
from reelworker.jobs import reel  # noqa: E402
from make_from_spec import check  # noqa: E402

STORE = config.APP / "local_store"


class LocalDB:
    """Just enough of reelworker.db.Supabase for one reel: the job row in a JSON file, uploads as local files, every
    other read empty (code paths already fall back to the Mac's .env and the built-in We One profile)."""
    meter = {}

    def __init__(self, job_file: Path):
        self.job_file = job_file
        self.job = json.loads(job_file.read_text()) if job_file.exists() else None

    def _save(self):
        self.job_file.parent.mkdir(parents=True, exist_ok=True)
        self.job_file.write_text(json.dumps(self.job, ensure_ascii=False, indent=1, default=str))

    def slow(self):
        return 0

    def flush_meter(self):
        return {}

    def rpc(self, fn, **args):
        return True if fn == "renew_lease" else None

    def select(self, table, query=""):
        if table == "jobs" and self.job and f"id=eq.{self.job['id']}" in query:
            m = re.search(r"select=([^&]*)", query)
            if m and m.group(1) not in ("*", ""):
                cols = [c.split(":")[-1] for c in m.group(1).split(",")]
                return [{c: self.job.get(c) for c in cols}]
            return [dict(self.job)]
        return []

    def insert(self, table, row, upsert=False, minimal=False):
        if table == "jobs" and self.job is None:
            self.job = {**row, "output": row.get("output") or {}, "created_at": datetime.now(timezone.utc).isoformat()}
            self._save()
            return [dict(self.job)]
        return [] if minimal else [row]

    def update(self, table, match: str, values: dict, columns: str = "id"):
        if table == "jobs" and self.job and f"id=eq.{self.job['id']}" in match:
            self.job.update(values); self._save()
            return [dict(self.job)]
        return []

    def delete(self, table, match: str):
        return None

    def upload(self, path_in_bucket: str, file_path, content_type: str):
        dst = STORE / path_in_bucket; dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(file_path, dst)
        return str(dst)

    def sign(self, path_in_bucket: str, expires: int = 7200) -> str:
        return (STORE / path_in_bucket).as_uri()

    def download(self, path_in_bucket: str, dest, bucket: str = None) -> None:
        src = STORE / path_in_bucket
        if not src.exists():
            raise FileNotFoundError(f"{path_in_bucket} is not in the local store (no cloud access in local mode)")
        shutil.copy(src, dest)

    def delete_objects(self, paths):
        return None

    def list_objects(self, prefix=""):
        return []


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S", stream=sys.stdout, force=True)
    a = argparse.ArgumentParser()
    a.add_argument("spec"); a.add_argument("--video", default="", choices=["", "presenter_studio", "presenter_fast"])
    a.add_argument("--brand", default="weone"); a.add_argument("--ad", action="store_true"); a.add_argument("--job")
    o = a.parse_args()
    spec = json.loads(Path(o.spec).read_text(encoding="utf-8")); check(spec)
    jid = o.job or str(uuid.uuid4())
    db = LocalDB(config.JOBS_DIR / jid / "local_job.json")
    wid = config.WORKER_ID
    if db.job is None:
        inp = {"source": "spec", "brand": o.brand, "concept": {"title": spec["title"]}, **({"video": o.video} if o.video else {}),
               **({"ad": True} if o.ad else {})}
        db.insert("jobs", {"id": jid, "kind": "reel", "brand": o.brand, "status": "running", "lease_owner": wid, "attempts": 1, "input": inp})
    else:
        db.update("jobs", f"id=eq.{jid}", {"status": "running", "lease_owner": wid, "error": None})
    ctx = JobContext(db, db.job, wid)
    if not ctx.is_done("script"):
        ctx.path("spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
        ctx.merge_output({"title": spec["title"], "claims": spec["claims"]})
        ctx.mark_done("script")
    print(f"job {jid}  (resume with --job {jid})", flush=True)
    try:
        reel.run(ctx)
    except Exception as e:  # noqa: BLE001
        db.update("jobs", f"id=eq.{jid}", {"status": "failed", "error": str(e)[-300:]})
        print(f"FAILED: {e}\nresume with: --job {jid}", flush=True)
        raise
    out = db.job.get("output") or {}
    vids = sorted(Path(config.DELIVERABLES).rglob(f"*_{jid[:8]}_master.mp4")) or sorted((STORE / jid).glob("ig.mp4"))
    if not vids:
        print("finished, but no video file was found in", ctx.dir); return
    safe = re.sub(r"[^A-Za-z0-9]+", "_", spec["title"]).strip("_")[:70]
    dst = config.APP / "deliverables" / "local" / f"{safe}_{jid[:8]}.mp4"; dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(vids[-1], dst)
    print(f"finished: {db.job.get('status')}  video: {dst}", flush=True)
    if out.get("qc"):
        print("quality check:", json.dumps(out["qc"], ensure_ascii=False)[:600], flush=True)


if __name__ == "__main__":
    main()

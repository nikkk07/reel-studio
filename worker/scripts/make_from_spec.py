"""Make one reel from a finished, approved script (spec.json) on this Mac, skipping the Scriptwriter.
Used for ads, where every word is approved before production. The job appears in Reel Studio like any reel (progress, review,
download); an ad is never auto-posted.

    ~/ReelStudio/.venv/bin/python worker/scripts/make_from_spec.py ad_spec.json --video presenter_studio --ad
    ... --job <id>      resume the same job after a stop (finished stages are kept)

It runs under its own worker id, keeps its own lease, and on failure marks the job failed (never back in the queue), so the
always-on worker never picks it up and makes it in a different style.
"""
import argparse, json, os, sys, threading, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("REELSTUDIO_WORKER_ID", "spec-" + (os.uname().nodename.split(".")[0] or "mac"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reelworker import config  # noqa: E402
from reelworker.db import Supabase  # noqa: E402
from reelworker.runner import JobContext  # noqa: E402
from reelworker.jobs import reel  # noqa: E402

REQUIRED = ("title", "voice", "mood", "groups", "shots", "graphics", "claims")


def check(spec):
    miss = [k for k in REQUIRED if k not in spec]
    if miss:
        raise SystemExit(f"spec.json is missing {', '.join(miss)}")
    gids = {g["id"] for g in spec["groups"]}
    bad = [x.get("id", x.get("type")) for x in spec["shots"] + spec["graphics"] if x.get("at_group") not in gids]
    if bad:
        raise SystemExit(f"these shots/graphics point at unknown groups: {bad}")


def main():
    a = argparse.ArgumentParser()
    a.add_argument("spec"); a.add_argument("--video", default="", choices=["", "presenter_studio", "presenter_fast"])
    a.add_argument("--brand", default="weone"); a.add_argument("--ad", action="store_true"); a.add_argument("--job")
    o = a.parse_args()
    spec = json.loads(Path(o.spec).read_text(encoding="utf-8")); check(spec)
    db = Supabase(); wid = config.WORKER_ID
    lease = lambda: (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat()  # noqa: E731
    if o.job:
        jid = o.job
        db.update("jobs", f"id=eq.{jid}", {"status": "running", "lease_owner": wid, "lease_expires_at": lease(), "error": None})
    else:
        jid = str(uuid.uuid4())
        inp = {"source": "spec", "brand": o.brand, "concept": {"title": spec["title"]}, **({"video": o.video} if o.video else {}),
               **({"ad": True} if o.ad else {})}
        db.insert("jobs", {"id": jid, "kind": "reel", "brand": o.brand, "status": "running", "lease_owner": wid, "lease_expires_at": lease(),
                           "attempts": 1, "input": inp})
    job = db.select("jobs", f"select=*&id=eq.{jid}")[0]
    ctx = JobContext(db, job, wid)
    if not ctx.is_done("script"):
        ctx.path("spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
        ctx.merge_output({"title": spec["title"], "claims": spec["claims"]})
        ctx.mark_done("script")
    print(f"job {jid}", flush=True)
    done = threading.Event()

    def keep():
        while not done.wait(30):
            try:
                db.update("jobs", f"id=eq.{jid}&lease_owner=eq.{wid}", {"lease_expires_at": lease()})
            except Exception as e:  # noqa: BLE001
                print("lease renew failed:", e, flush=True)
    threading.Thread(target=keep, daemon=True).start()
    try:
        reel.run(ctx)
        print("finished:", (db.select("jobs", f"select=status&id=eq.{jid}") or [{}])[0].get("status"), flush=True)
    except Exception as e:  # noqa: BLE001
        db.update("jobs", f"id=eq.{jid}", {"status": "failed", "lease_owner": None, "lease_expires_at": None,
                                          "error": f"{str(e)[-300:]} (resume with --job {jid})"})
        raise
    finally:
        done.set()


if __name__ == "__main__":
    main()

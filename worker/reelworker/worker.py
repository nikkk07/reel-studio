"""Mac worker: heartbeat, ping answers, and two job lanes. Runs forever under launchd.

    python -m reelworker.worker
"""
import json, logging, logging.handlers, os, shutil, signal, sys, threading, time, traceback
from datetime import datetime, timedelta, timezone
from . import config, brain
from .db import Supabase, now_iso
from .runner import JobContext, QuotaError, TransientError, Cancelled
from . import procs, instagram
from urllib.parse import quote

log = logging.getLogger("reelworker")
STOP = threading.Event()
CURRENT = {"A": None, "B": None}
STATUS = {"claude_ok": None, "claude_note": None, "claude_checked": 0.0}


def setup_logging():
    log.setLevel(logging.INFO)
    fh = logging.handlers.RotatingFileHandler(config.LOG_DIR / "worker.log", maxBytes=5_000_000, backupCount=5)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(message)s"))
    sh = logging.StreamHandler(sys.stdout); sh.setFormatter(fh.formatter)
    log.addHandler(fh); log.addHandler(sh)


# ------------------------------------------------------------------ daily reel cap (local state survives restarts)
def _cap_file():
    return config.APP / "state" / "daily.json"


def reels_today() -> int:
    f = _cap_file()
    try:
        d = json.loads(f.read_text())
        return d["count"] if d.get("date") == datetime.now().strftime("%Y-%m-%d") else 0
    except Exception:  # noqa: BLE001
        return 0


def bump_reels_today():
    f = _cap_file(); f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"date": datetime.now().strftime("%Y-%m-%d"), "count": reels_today() + 1}))


CHECKING = threading.Event()
CODE_SIG0 = None


def refresh_claude(db):
    """Fresh Claude check (one tiny call). Triggered by the heartbeat every 30 min, or by a web 'Connect' ping."""
    CHECKING.set()
    try:
        ok, note = brain.health_check()
        STATUS.update(claude_ok=ok, claude_note=note, claude_checked=time.time())
        if db is not None:
            db.update("workers", f"id=eq.{config.WORKER_ID}", {"claude_ok": ok, "claude_note": note, "claude_checked_at": now_iso()})
    except Exception as e:  # noqa: BLE001
        log.warning("claude check failed: %s", e)
    finally:
        CHECKING.clear()


# ------------------------------------------------------------------ heartbeat + claude check + housekeeping
def heartbeat_loop(db: Supabase):
    last_prune = 0.0
    while not STOP.is_set():
        try:
            if time.time() - STATUS["claude_checked"] > 1800 and not CHECKING.is_set():
                refresh_claude(None)
            disk = shutil.disk_usage(config.JOBS_DIR)
            busy = any(CURRENT.values())
            db.insert("workers", {
                "id": config.WORKER_ID, "last_seen": now_iso(), "status": "busy" if busy else "idle", "version": config.VERSION,
                "claude_ok": STATUS["claude_ok"], "claude_note": STATUS["claude_note"],
                "claude_checked_at": now_iso(datetime.fromtimestamp(STATUS["claude_checked"], timezone.utc)) if STATUS["claude_checked"] else None,
                "info": {"disk_free_gb": round(disk.free / 1e9, 1), "current": CURRENT, "reels_today": reels_today(),
                         "daily_cap": config.DAILY_REEL_CAP, "blocked": STATUS.get("blocked"), "selftest": STATUS.get("selftest"),
                         "cap_hit": reels_today() >= config.DAILY_REEL_CAP},
            }, upsert=True)
            if time.time() - last_prune > 86400:
                prune(db); last_prune = time.time()
        except Exception as e:  # noqa: BLE001
            log.warning("heartbeat failed: %s", e)
        STOP.wait(15)


def prune(db: Supabase):
    try:
        cutoff = now_iso(datetime.now(timezone.utc) - timedelta(days=30))
        db.delete("job_events", f"ts=lt.{cutoff}")
        db.delete("pings", f"created_at=lt.{now_iso(datetime.now(timezone.utc) - timedelta(days=1))}")
        old = db.select("jobs", f"select=id,output&kind=eq.reel&created_at=lt.{cutoff}&output->>storage_prefix=not.is.null")
        for j in old:
            prefix = j["output"].get("storage_prefix")
            files = [f"{prefix}/{o['name']}" for o in db.list_objects(prefix)]
            db.delete_objects(files)
            db.update("jobs", f"id=eq.{j['id']}", {"output": {**j["output"], "storage_prefix": None, "pruned": True}})
        # local disk: job folders (raw footage, takes, chunks) older than 14 days; masters stay in deliverables/
        n = 0
        for d in config.JOBS_DIR.iterdir():
            if d.is_dir() and time.time() - d.stat().st_mtime > 14 * 86400 and not any(d.name == (c or {}).get("id") for c in CURRENT.values()):
                shutil.rmtree(d, ignore_errors=True); n += 1
        log.info("prune done (%d old reels in cloud, %d local job folders)", len(old), n)
    except Exception as e:  # noqa: BLE001
        log.warning("prune failed: %s", e)


def ping_loop(db: Supabase):
    while not STOP.is_set():
        try:
            since = now_iso(datetime.now(timezone.utc) - timedelta(minutes=2))
            pings = db.select("pings", f"select=id&answered_at=is.null&created_at=gt.{since}")
            if pings and time.time() - STATUS["claude_checked"] > 600 and not CHECKING.is_set():
                CHECKING.set(); threading.Thread(target=refresh_claude, args=(db,), daemon=True, name="claude-check").start()
            for p in pings:
                db.update("pings", f"id=eq.{p['id']}&answered_at=is.null", {
                    "answered_at": now_iso(), "worker_id": config.WORKER_ID,
                    "reply": {"claude_checking": CHECKING.is_set(), "claude_ok": STATUS["claude_ok"], "claude_note": STATUS["claude_note"], "busy": CURRENT,
                              "reels_today": reels_today(), "daily_cap": config.DAILY_REEL_CAP, "version": config.VERSION}})
        except Exception as e:  # noqa: BLE001
            log.warning("ping loop: %s", e)
        STOP.wait(3)


# ------------------------------------------------------------------ lanes
def lease_keeper(db, ctx: JobContext, done: threading.Event):
    """Renews the lease every 10 s. If the job is no longer ours (Stop / Resume on the website, or another claim),
    the job is stopped at once: the flag ends the stage loop and every helper process of this job is killed."""
    misses = 0
    while not done.wait(10):
        try:
            ok = db.rpc("renew_lease", p_job=ctx.id, p_worker=config.WORKER_ID, p_lease_seconds=90)
            misses = 0
            if not ok:
                ctx.cancelled.set()
                n = procs.kill_job(ctx.id)
                log.info("[%s] stopped from the website; %d helper process(es) ended", ctx.id[:8], n)
                return
        except Exception as e:  # noqa: BLE001  (network blip: keep working, the 90 s lease covers ~8 missed renewals)
            misses += 1
            log.warning("lease renew failed (%d): %s", misses, e)


def _release(db, ctx):
    """Give the job back after a Stop so the website can offer Resume immediately."""
    try:
        db.update("jobs", f"id=eq.{ctx.id}&lease_owner=eq.{quote(config.WORKER_ID)}&status=neq.running",
                  {"lease_owner": None, "lease_expires_at": None})
    except Exception as e:  # noqa: BLE001
        log.warning("lease release failed: %s", e)


def run_job(db, job, handler):
    ctx = JobContext(db, job, config.WORKER_ID)
    done = threading.Event()
    threading.Thread(target=lease_keeper, args=(db, ctx, done), daemon=True, name=f"lease-{ctx.id[:6]}").start()
    try:
        handler(ctx)
        if job["kind"] == "reel" and not job.get("parent_id"):
            bump_reels_today()          # the daily cap counts new finished reels, not retries, revisions or cancelled ones
    except Exception as e:  # noqa: BLE001
        if isinstance(e, Cancelled) or ctx.cancelled.is_set():
            # a killed helper surfaces as an ordinary error: never turn a Stop into a retry
            done.set(); procs.kill_job(ctx.id); ctx.log("stopped from the website; finished steps are kept for Resume", "warn")
            _release(db, ctx)
        elif isinstance(e, QuotaError):
            reset = e.reset_at or (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            ctx.log(f"Claude usage limit reached, waiting until {reset}", "warn")
            db.update("jobs", ctx.owned(), {"status": "waiting_quota", "not_before": reset, "lease_owner": None, "lease_expires_at": None})
            STATUS.update(claude_ok=False, claude_note=f"usage limit until {reset}", claude_checked=time.time())
        else:
            # Any failure: retry automatically from the last finished stage (checkpoints), at most 3 failures in total.
            tb = traceback.format_exc(limit=6)
            stage = STAGE_NAMES.get(ctx.job.get("stage") or "", "Setup")
            fails = int((ctx.job.get("output") or {}).get("fail_count", 0)) + 1
            detail = str(e).strip().splitlines()[-1][:220] if str(e).strip() else type(e).__name__
            ctx.log(f"{stage} step failed: {detail}", "error", trace=tb[-1500:])
            try:
                ctx.merge_output({"fail_count": fails})
            except Exception:  # noqa: BLE001
                pass
            if fails < MAX_FAILS:
                wait_min = 2 if fails == 1 else 6
                retry_at = datetime.now(timezone.utc) + timedelta(minutes=wait_min)
                db.update("jobs", ctx.owned(), {"status": "queued", "not_before": now_iso(retry_at), "lease_owner": None, "lease_expires_at": None,
                          "error": f"{stage} step failed ({detail}). Retrying automatically from {stage} (attempt {fails + 1} of {MAX_FAILS})."})
            else:
                db.update("jobs", ctx.owned(), {"status": "failed", "lease_owner": None, "lease_expires_at": None,
                          "error": f"{stage} step failed {MAX_FAILS} times ({detail}). Saved progress is kept; press Resume to continue from {stage}."})
    finally:
        done.set()


STAGE_NAMES = {"script": "Script", "voice": "Voice", "footage": "Footage", "render": "Render", "qc": "Check", "publish": "Publish", "research": "Research"}
MAX_FAILS = 3
MIN_FREE_GB = float(config.get("REELSTUDIO_MIN_FREE_GB", "6"))


def startup_check():
    """Run the pipeline's own self-test once per start (files, ffmpeg, a 3-second render + master + QC sheet).
    Reels wait until it passes, and the result is visible on the website through the heartbeat."""
    from . import selftest
    fails = []
    for name, fn in (("files", selftest.files), ("ffmpeg", selftest.tools), ("render", selftest.render)):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            fails.append(f"{name}: {str(e)[:160]}")
    STATUS["selftest"] = {"ok": not fails, "fails": fails, "at": now_iso()}
    log.info("startup self-check %s %s", "passed" if not fails else "FAILED", "; ".join(fails))


def disk_free_gb():
    return shutil.disk_usage(config.JOBS_DIR).free / 1e9


def lane_loop(db: Supabase, lane: str, kinds: list):
    from .jobs import suggest, optimize, reel
    handlers = {"suggest": suggest.run, "optimize": optimize.run, "reel": reel.run}
    while not STOP.is_set():
        try:
            cap_hit = lane == "B" and reels_today() >= config.DAILY_REEL_CAP    # after the cap only revisions (Request changes) run
            st = STATUS.get("selftest")
            if lane == "B" and (st is None or not st["ok"]):
                if st is not None:
                    STATUS["blocked"] = "a failed self-check (" + "; ".join(st["fails"])[:200] + ")"
                STOP.wait(30); continue
            if lane == "B" and disk_free_gb() < MIN_FREE_GB:      # a reel needs a few GB of working space
                STATUS["blocked"] = f"low disk space ({disk_free_gb():.1f} GB free, needs {MIN_FREE_GB:.0f} GB)"
                STOP.wait(60); continue
            STATUS["blocked"] = None
            if code_signature() != CODE_SIG0:          # new code installed: let main() restart before taking more work
                STOP.wait(5); continue
            job = db.rpc("claim_next", p_worker=config.WORKER_ID, p_kinds=kinds, p_lease_seconds=90, p_revisions_only=cap_hit)
            if not job or not job.get("id"):
                STOP.wait(15 if cap_hit else 4); continue
            CURRENT[lane] = {"id": job["id"], "kind": job["kind"]}
            log.info("lane %s claimed %s %s (attempt %s)", lane, job["kind"], job["id"], job.get("attempts"))
            run_job(db, job, handlers[job["kind"]])
        except Exception as e:  # noqa: BLE001
            log.error("lane %s loop error: %s", lane, e); STOP.wait(10)
        finally:
            CURRENT[lane] = None


def main():
    setup_logging()
    log.info("reelworker %s starting as %s (root %s)", config.VERSION, config.WORKER_ID, config.ROOT)
    db = Supabase()
    global CODE_SIG0
    CODE_SIG0 = code_signature()
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    threads = [threading.Thread(target=heartbeat_loop, args=(db,), name="heartbeat", daemon=True),
               threading.Thread(target=ping_loop, args=(db,), name="ping", daemon=True),
               threading.Thread(target=lane_loop, args=(db, "A", ["suggest", "optimize"]), name="laneA", daemon=True),
               threading.Thread(target=lane_loop, args=(db, "B", ["reel"]), name="laneB", daemon=True),
               threading.Thread(target=instagram.loop, args=(db, STOP), name="instagram", daemon=True)]
    for t in threads:
        t.start()
    threading.Thread(target=startup_check, name="selfcheck", daemon=True).start()
    sig0, last_check = CODE_SIG0, time.time()
    try:
        while not STOP.is_set():
            time.sleep(1)
            if time.time() - last_check > 30:
                last_check = time.time()
                if code_signature() != sig0 and not any(CURRENT.values()):
                    # new code was installed: exit while idle; launchd starts the new version within ~20 s
                    log.info("new worker code detected; restarting to load it")
                    logging.shutdown(); os._exit(0)
    except KeyboardInterrupt:
        STOP.set()
    log.info("reelworker stopping")


def code_signature():
    from pathlib import Path
    base = Path(__file__).resolve().parent
    return max((f.stat().st_mtime for f in list(base.rglob("*.py")) + list(base.rglob("*.md"))), default=0)


if __name__ == "__main__":
    main()

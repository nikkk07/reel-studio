"""Job context, checkpoints and the error types that drive retries / quota waits."""
import json, logging, threading, time
from urllib.parse import quote
from pathlib import Path
from . import config
from .db import now_iso

log = logging.getLogger("reelworker")


class QuotaError(RuntimeError):
    """Claude usage limit reached; job waits until reset_at (ISO time) and resumes."""
    def __init__(self, msg, reset_at=None):
        super().__init__(msg); self.reset_at = reset_at


class TransientError(RuntimeError):
    """Network / provider hiccup: the job is re-queued (max 3 attempts)."""


class Cancelled(RuntimeError):
    pass


class JobContext:
    def __init__(self, db, job: dict, worker_id: str):
        self.db, self.job, self.worker_id = db, job, worker_id
        self.id = job["id"]
        self.dir = config.JOBS_DIR / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / "state.json"
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {"done": [], "data": {}}
        self.cancelled = threading.Event()
        self._last_progress = 0.0
        self._trace, self._trace_sent = (job.get("output") or {}).get("trace") or {}, 0.0

    # ---- persistence
    def save(self):
        tmp = self.state_path.with_suffix(".tmp"); tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1)); tmp.replace(self.state_path)

    def is_done(self, stage):
        return stage in self.state["done"]

    def mark_done(self, stage, **data):
        if stage not in self.state["done"]:
            self.state["done"].append(stage)
        self.state["data"].update(data); self.save()

    @property
    def data(self):
        return self.state["data"]

    def path(self, *parts) -> Path:
        p = self.dir.joinpath(*parts); p.parent.mkdir(parents=True, exist_ok=True); return p

    # ---- reporting (best effort: a DB hiccup must never kill a render)
    def log(self, message, level="info", **data):
        log.log(logging.WARNING if level in ("warn", "error") else logging.INFO, "[%s] %s", self.id[:8], message)
        try:
            self.db.insert("job_events", {"job_id": self.id, "level": level, "message": message[:2000], "data": data or None})
        except Exception as e:  # noqa: BLE001
            log.warning("event insert failed: %s", e)

    def progress(self, stage, value):
        self.check_cancel()
        if stage != "finished":
            self.job["stage"] = stage
        now = time.time()
        if value >= 1 or now - self._last_progress > 3:
            self._last_progress = now
            try:
                self.db.update("jobs", f"id=eq.{self.id}", {"stage": stage, "progress": round(float(value), 3)})
            except Exception as e:  # noqa: BLE001
                log.warning("progress update failed: %s", e)

    def merge_output(self, values: dict):
        out = {**(self.job.get("output") or {}), **values}; self.job["output"] = out
        self.db.update("jobs", f"id=eq.{self.id}", {"output": out})

    def owned(self):
        """PostgREST filter: this job, still running and still leased by this Mac (a Stop or Resume from the site ends that)."""
        return f"id=eq.{self.id}&status=eq.running&lease_owner=eq.{quote(self.worker_id)}"

    def finish(self, status, **output):
        out = {**(self.job.get("output") or {}), **output}; self.job["output"] = out
        rows = self.db.update("jobs", self.owned(), {"status": status, "output": out, "progress": 1.0 if status in ("review", "done") else self.job.get("progress", 0),
                                                    "lease_owner": None, "lease_expires_at": None})
        if not rows:   # stopped (or resumed elsewhere) from the website while finishing: never overwrite that decision
            self.cancelled.set()
            raise Cancelled("stopped from the website before it could finish")

    def trace(self, stage, node, label, status="run", info="", flush=False):
        """Sub-steps shown as a node graph on the website. status: wait | run | done | fail."""
        self._trace.setdefault(stage, {})[node] = {"label": label, "status": status, "info": str(info)[:80], "t": round(time.time())}
        if flush or time.time() - self._trace_sent > 2.5:
            self._trace_sent = time.time()
            try:
                self.merge_output({"trace": self._trace})
            except Exception as e:  # noqa: BLE001
                log.warning("trace update failed: %s", e)

    def check_cancel(self):
        if self.cancelled.is_set():
            raise Cancelled("cancelled by user")


def run_stages(ctx: JobContext, stages):
    """stages: list of (name, weight, fn). Completed stages are skipped after a crash/restart."""
    total = sum(w for _, w, _ in stages); acc = 0.0
    for name, weight, fn in stages:
        if ctx.is_done(name):
            acc += weight; continue
        ctx.progress(name, acc / total); ctx.log(f"stage {name} started")
        t0 = time.time()
        ctx.merge_output({"stage_started": {**(ctx.job.get("output") or {}).get("stage_started", {}), name: round(t0)}})
        fn(ctx, lambda f, a=acc: ctx.progress(name, (a + weight * max(0.0, min(1.0, f))) / total))
        ctx.mark_done(name); acc += weight
        ctx.merge_output({"timings": {**(ctx.job.get("output") or {}).get("timings", {}), name: round(time.time() - t0)},
                          "trace": ctx._trace})
        ctx.log(f"stage {name} done in {time.time() - t0:.0f}s")
    ctx.progress("finished", 1.0)

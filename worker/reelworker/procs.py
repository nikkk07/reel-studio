"""Stop every helper process that belongs to one job (Claude, voice model, frame renderers, ffmpeg) within seconds.

A process belongs to a job when it descends from this worker and its command line or working folder mentions the job id
(every job works inside ~/ReelStudio/jobs/<id>). Its whole subtree is stopped: SIGTERM first, SIGKILL after a grace period.
"""
import os, signal, subprocess, sys, time
from pathlib import Path


_TRACKED = {}          # pid -> folder it works in (registered by callers; works even when ps/lsof give no hint)


def track(popen, folder):
    _TRACKED[popen.pid] = str(folder)
    for pid in [p for p in _TRACKED if p != popen.pid and not _alive(p)]:
        _TRACKED.pop(pid, None)
    return popen


def _table():
    out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,command="], capture_output=True, text=True, timeout=20).stdout
    rows = {}
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            rows[int(parts[0])] = (int(parts[1]), parts[2] if len(parts) > 2 else "")
    return rows


def _cwd(pid):
    try:
        if sys.platform.startswith("linux"):
            return os.readlink(f"/proc/{pid}/cwd")
        r = subprocess.run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], capture_output=True, text=True, timeout=10).stdout
        return next((l[1:] for l in r.splitlines() if l.startswith("n")), "")
    except Exception:  # noqa: BLE001
        return ""


def _descendants(rows, root):
    kids = {}
    for pid, (ppid, _) in rows.items():
        kids.setdefault(ppid, []).append(pid)
    out, stack = [], list(kids.get(root, []))
    while stack:
        p = stack.pop(); out.append(p); stack.extend(kids.get(p, []))
    return out


def kill_job(job_id: str, grace: float = 4.0) -> int:
    """Returns how many processes were stopped."""
    try:
        rows = _table()
    except Exception:  # noqa: BLE001
        return 0
    me = os.getpid()
    targets = set()
    for pid in _descendants(rows, me):
        if pid in targets:
            continue
        cmd = rows[pid][1]
        if job_id in cmd or job_id in _TRACKED.get(pid, "") or job_id in _cwd(pid):
            targets.add(pid); targets.update(_descendants(rows, pid))
    targets.discard(me)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        alive = []
        for pid in targets:
            try:
                os.kill(pid, sig); alive.append(pid)
            except (ProcessLookupError, PermissionError):
                pass
        if not alive or sig == signal.SIGKILL:
            break
        t0 = time.time()
        while time.time() - t0 < grace and any(_alive(p) for p in alive):
            time.sleep(0.2)
    return len(targets)


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:   # reaped zombies of our own children count as gone
        return Path(f"/proc/{pid}/status").read_text().find("State:\tZ") < 0 if sys.platform.startswith("linux") else True
    except Exception:  # noqa: BLE001
        return True

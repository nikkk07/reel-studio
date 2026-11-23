"""Mac first, cloud as backup: while the Mac is online, the website's live view (reels in progress, the review queue, the
Mac's own status) comes from the Mac instead of Supabase, through a free Cloudflare quick tunnel. When the Mac or the tunnel
is down, the website reads Supabase as before, so nothing depends on this.

    Mirror  - the live reels, kept current from the Mac's own saves (no read back), the database's wake pushes (realtime.py)
              and a tiny "what changed" read; a full read only when a reel changed outside the Mac
    serve() - a small read-only HTTP API on 127.0.0.1: /v1/health (no data) and /v1/poll (signed-in members only; the
              website's Supabase sign-in is checked with Supabase once and remembered while it is valid)
    Tunnel  - runs `cloudflared tunnel --url` and publishes the address in settings 'mac_api' for the website
"""
import hashlib, json, logging, os, re, shutil, signal, subprocess, threading, time, base64
from urllib.parse import quote
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

log = logging.getLogger("reelworker")

COLS = ("id,brand,kind,status,stage,progress,input,output,error,not_before,created_at,updated_at,attempts,parent_id,"
        "lease_owner,lease_expires_at")
ACTIVE = ("queued", "running", "waiting_quota")
LIVE = ACTIVE + ("review",)
STOPPED = ("failed", "cancelled")
STOPPED_H = 12


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts(s) -> float:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:  # noqa: BLE001
        return 0.0


class Mirror:
    """The reels the website's live view shows, as the website would read them from Supabase."""

    FULL_EVERY = 600          # a complete re-read every 10 min while someone is looking (heals anything missed)
    DELTA_EVERY = 30          # a "what changed" read at least this often while someone is looking

    def __init__(self, db, waker=None):
        self.db, self.waker = db, waker
        self.rows = {}                    # id -> full row
        self.cursor = None                # newest updated_at seen
        self.full_at = 0.0
        self.delta_at = 0.0
        self.wake_seen = -1
        self.lock = threading.Lock()
        self.reads = {"full": 0, "delta": 0, "rows": 0}

    # the Mac's own saves (db.update on jobs): applied here, never read back
    def on_write(self, table, match, values, rows):
        if table != "jobs" or not rows:
            return
        with self.lock:
            for r in rows:
                cur = self.rows.get(str(r.get("id")))
                if cur is None:
                    continue
                cur.update({k: v for k, v in values.items()})
                if r.get("updated_at"):
                    cur["updated_at"] = r["updated_at"]

    def _keep(self, r, now):
        if r.get("kind") != "reel":
            return False
        if r.get("status") in LIVE:
            return True
        return r.get("status") in STOPPED and now - _ts(r.get("updated_at")) < STOPPED_H * 3600

    def _full(self):
        since = _iso(datetime.now(timezone.utc) - timedelta(hours=STOPPED_H))
        live = self.db.select("jobs", f"select={COLS}&kind=eq.reel&status=in.({','.join(LIVE)})&order=created_at.asc&limit=50")
        stopped = self.db.select("jobs", f"select={COLS}&kind=eq.reel&status=in.({','.join(STOPPED)})&updated_at=gte.{since}"
                                         "&order=updated_at.desc&limit=12")
        rows = {str(r["id"]): r for r in (live or []) + (stopped or [])}
        self.rows = rows
        self.cursor = max((r.get("updated_at") or "" for r in rows.values()), default=None) or _iso(datetime.now(timezone.utc) - timedelta(minutes=5))
        self.full_at = self.delta_at = time.time()
        self.reads["full"] += 1

    def _delta(self):
        changed = self.db.select("jobs", f"select=id,status,updated_at&kind=eq.reel&updated_at=gt.{quote(self.cursor, safe='')}&order=updated_at.asc&limit=200") or []
        self.delta_at = time.time(); self.reads["delta"] += 1
        if len(changed) >= 200:
            return self._full()
        need = []
        for c in changed:
            cid = str(c["id"]); cur = self.rows.get(cid)
            if cur is not None and cur.get("updated_at") == c.get("updated_at"):
                continue                                   # the Mac's own save, already applied
            if c.get("status") in LIVE or c.get("status") in STOPPED or cur is not None:
                need.append(cid)
        if changed:
            self.cursor = max(self.cursor or "", max(c.get("updated_at") or "" for c in changed))
        for k in range(0, len(need), 40):
            ids = ",".join(need[k:k + 40])
            for r in self.db.select("jobs", f"select={COLS}&id=in.({ids})") or []:
                self.rows[str(r["id"])] = r; self.reads["rows"] += 1

    def sync(self, force=False):
        with self.lock:
            now = time.time()
            woke = self.waker is not None and self.waker.gen.get("job", 0) != self.wake_seen
            if self.waker is not None:
                self.wake_seen = self.waker.gen.get("job", 0)
            listening = self.waker is not None and self.waker.connected
            if force or not self.full_at or now - self.full_at > self.FULL_EVERY:
                self._full()
            elif woke or not listening and now - self.delta_at > 5 or now - self.delta_at > self.DELTA_EVERY:
                self._delta()
            now = time.time()
            for k in [k for k, r in self.rows.items() if not self._keep(r, now)]:
                self.rows.pop(k, None)

    def view(self):
        """(live, stopped) exactly as the website's two Supabase reads would return them."""
        rows = list(self.rows.values())
        live = sorted([r for r in rows if r.get("status") in LIVE], key=lambda r: r.get("created_at") or "")[:50]
        stopped = sorted([r for r in rows if r.get("status") in STOPPED], key=lambda r: r.get("updated_at") or "", reverse=True)[:12]
        return live, stopped


class Auth:
    """Website sign-ins: checked with Supabase once, then remembered until they expire (at most 10 min)."""

    def __init__(self, db):
        self.db = db
        self.cache = {}                  # sha256(token) -> (ok, until)
        self.members = (set(), 0.0)
        self.checks = []                 # times of recent checks with Supabase (abuse cap)
        self.lock = threading.Lock()

    def _members(self):
        emails, at = self.members
        if time.time() - at > 600:
            rows = self.db.select("members", "select=email") or []
            emails = {str(r.get("email") or "").lower() for r in rows}
            self.members = (emails, time.time())
        return emails

    @staticmethod
    def _exp(token) -> float:
        try:
            part = token.split(".")[1]; part += "=" * (-len(part) % 4)
            return float(json.loads(base64.urlsafe_b64decode(part)).get("exp") or 0)
        except Exception:  # noqa: BLE001
            return 0.0

    def check(self, header: str) -> bool:
        if not header or not header.lower().startswith("bearer "):
            return False
        token = header.split(" ", 1)[1].strip()
        if len(token) < 20 or len(token) > 8192:
            return False
        h = hashlib.sha256(token.encode()).hexdigest(); now = time.time()
        with self.lock:
            hit = self.cache.get(h)
            if hit and hit[1] > now:
                return hit[0]
            self.checks = [t for t in self.checks if now - t < 60]
            if len(self.checks) >= 20:
                return False                              # too many new sign-ins a minute: refuse, nothing asked
            self.checks.append(now)
        exp = self._exp(token)
        ok = False
        if exp > now:
            try:
                r = self.db._req("GET", "/auth/v1/user", retries=1, headers={"Authorization": f"Bearer {token}"})
                email = str((r.json() or {}).get("email") or "").lower()
                ok = bool(email) and email in self._members()
            except Exception:  # noqa: BLE001
                ok = False
        with self.lock:
            self.cache[h] = (ok, min(exp, now + 600) if ok else now + 60)
            if len(self.cache) > 500:
                self.cache = {k: v for k, v in self.cache.items() if v[1] > now}
        return ok


def allowed_origins():
    extra = [o.strip() for o in str(os.environ.get("REELSTUDIO_API_ORIGINS", "")).split(",") if o.strip()]
    return {"https://studio.weoneaviation.in", "https://weone-reelstudio.vercel.app", *extra}


def make_handler(mirror: Mirror, auth: Auth, worker_row, origins=None):
    origins = origins or allowed_origins()

    class H(BaseHTTPRequestHandler):
        server_version = "ReelStudioMac/1.0"

        def log_message(self, *a):          # no request logs (tokens live in headers, never printed)
            pass

        def _cors(self):
            o = self.headers.get("Origin") or ""
            if o in origins:
                self.send_header("Access-Control-Allow-Origin", o)
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Headers", "authorization, content-type")
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
                self.send_header("Access-Control-Max-Age", "600")

        def _json(self, code, obj):
            body = json.dumps(obj, separators=(",", ":")).encode()
            self.send_response(code); self._cors()
            self.send_header("Content-Type", "application/json"); self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204); self._cors(); self.send_header("Content-Length", "0"); self.end_headers()

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/v1/health":
                return self._json(200, {"ok": True, "at": _iso(datetime.now(timezone.utc))})
            if path != "/v1/poll":
                return self._json(404, {"error": "not found"})
            if not auth.check(self.headers.get("Authorization") or ""):
                return self._json(401, {"error": "sign in again"})
            try:
                mirror.sync()
                live, stopped = mirror.view()
                return self._json(200, {"live": live, "stopped": stopped, "worker": worker_row(), "at": _iso(datetime.now(timezone.utc))})
            except Exception as e:  # noqa: BLE001
                log.warning("mac api poll: %s", str(e)[:160])
                return self._json(503, {"error": "not ready"})
    return H


def serve(mirror, auth, worker_row, port: int, stop: threading.Event):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(mirror, auth, worker_row))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, name="macapi-http", daemon=True).start()
    log.info("mac api listening on 127.0.0.1:%s", port)
    stop.wait()
    httpd.shutdown()


class Tunnel:
    """A Cloudflare quick tunnel to the local API; its address goes to settings 'mac_api' for the website."""

    URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

    def __init__(self, db, port: int, state_dir: Path, binary=None, health=None, url_re=None):
        self.db, self.port, self.state_dir = db, port, Path(state_dir)
        self.binary = binary or self.find()
        self.health = health or self._health
        self.url_re = url_re or self.URL_RE
        self.url, self.proc, self.status = None, None, "starting"
        self.out = []                     # cloudflared's last lines (no secrets in a quick tunnel's output), for the log

    @staticmethod
    def find():
        for c in (os.environ.get("REELSTUDIO_CLOUDFLARED"), shutil.which("cloudflared"), "/opt/homebrew/bin/cloudflared", "/usr/local/bin/cloudflared"):
            if c and Path(c).exists():
                return c
        return None

    @staticmethod
    def _health(url) -> bool:
        import httpx
        try:
            return httpx.get(f"{url}/v1/health", timeout=10).status_code == 200
        except Exception:  # noqa: BLE001
            return False

    def publish(self, url):
        self.db.insert("settings", {"key": "mac_api", "value": {"url": url, "at": _iso(datetime.now(timezone.utc))}}, upsert=True)

    def _pidfile(self):
        return self.state_dir / "cloudflared.pid"

    def kill_stale(self):
        """A tunnel left behind by an earlier run of this Mac (a restart for new code) is stopped first."""
        try:
            pid = int(self._pidfile().read_text().strip())
            out = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True, timeout=5).stdout
            if "cloudflared" in out:
                os.kill(pid, signal.SIGTERM)
        except Exception:  # noqa: BLE001
            pass

    def start(self):
        home = self.state_dir / "cloudflared_home"; home.mkdir(parents=True, exist_ok=True)   # never the user's own tunnel config
        env = {**os.environ, "HOME": str(home)}
        self.kill_stale()
        self.proc = subprocess.Popen([self.binary, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{self.port}"],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
        try:
            self._pidfile().write_text(str(self.proc.pid))
        except Exception:  # noqa: BLE001
            pass
        found = {"url": None, "ready": False}

        self.out = []

        def read():
            for line in self.proc.stdout:
                self.out = (self.out + [line.strip()[:200]])[-15:]
                m = self.url_re.search(line)
                if m and not found["url"]:
                    found["url"] = m.group(0)
                if "Registered tunnel connection" in line:
                    found["ready"] = True
        threading.Thread(target=read, name="cloudflared-out", daemon=True).start()
        t0 = time.time()
        while time.time() - t0 < 60 and not (found["url"] and found["ready"]) and self.proc.poll() is None:
            time.sleep(0.5)
        return found["url"] if found["ready"] else None

    def stop_proc(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except Exception:  # noqa: BLE001
                self.proc.kill()

    def loop(self, stop: threading.Event):
        if not self.binary:
            self.status = "cloudflared is not installed (brew install cloudflared); the website reads Supabase"
            log.info("mac api: %s", self.status)
            return
        delay = 30
        while not stop.is_set():
            url = None
            try:
                url = self.start()                         # ready = cloudflared registered its connection to Cloudflare
                ok = bool(url)
                if ok:
                    self.url, delay = url, 30
                    while not stop.is_set() and self.proc.poll() is None:      # Supabase unreachable: keep the tunnel, retry the notice
                        try:
                            self.publish(url); break
                        except Exception as e:  # noqa: BLE001
                            self.status = f"tunnel up; waiting to tell the website ({str(e)[:60]})"
                            stop.wait(60)
                    self.status = "online"
                    log.info("mac api online at %s", url)
                    fails = 0                              # the address itself is checked now and then; only a long outage restarts it
                    while not stop.is_set() and self.proc.poll() is None:
                        stop.wait(300)
                        if stop.is_set():
                            break
                        fails = 0 if self.health(url) else fails + 1
                        if fails >= 6:
                            break
                else:
                    self.status = "the tunnel did not start; the website reads Supabase"
                    log.info("mac api: cloudflared said: %s", " | ".join(self.out[-8:]))
            except Exception as e:  # noqa: BLE001
                self.status = f"tunnel error: {str(e)[:120]}"
            self.stop_proc()
            if self.url:
                try:
                    self.publish(None)
                except Exception:  # noqa: BLE001
                    pass
                self.url = None
            if stop.is_set():
                break
            log.info("mac api: %s; trying again in %ss", self.status, delay)
            stop.wait(delay); delay = min(delay * 2, 1800)

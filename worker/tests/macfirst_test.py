"""Mac first, cloud as backup: Realtime wake-ups (a stand-in Realtime server), the live-reel mirror, sign-in checks, the
local API and the tunnel manager.   python tests/macfirst_test.py   (needs: pip install websockets; no network)"""
import asyncio, json, os, stat, sys, tempfile, threading, time, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent)); import pgsel  # noqa: E402
os.environ.setdefault("REELSTUDIO_APP_DIR", tempfile.mkdtemp())
import websockets  # noqa: E402
from reelworker import realtime, macapi  # noqa: E402

res = []
def ok(c, n): res.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n)

# ---------------------------------------------------------------- a stand-in Supabase Realtime server
SEEN = {"paths": [], "joins": [], "beats": 0, "pongs": 0, "conns": 0}
CMD = {"push": [], "close": False}
async def handler(ws):
    SEEN["conns"] += 1; SEEN["paths"].append(ws.request.path)
    join = json.loads(await ws.recv()); SEEN["joins"].append(join)
    await ws.send(json.dumps({"event": "phx_reply", "topic": join["topic"], "ref": join["ref"],
                              "payload": {"status": "ok", "response": {"postgres_changes": [{"id": 1, "event": "INSERT", "schema": "public", "table": "wake"}]}}}))
    await ws.send(json.dumps({"event": "system", "topic": join["topic"], "payload": {"status": "ok", "message": "Subscribed to PostgreSQL", "extension": "postgres_changes"}}))
    try:
        while True:
            if CMD["push"]:
                kind = CMD["push"].pop(0)
                await ws.send(json.dumps({"event": "postgres_changes", "topic": join["topic"], "payload": {
                    "data": {"type": "INSERT", "table": "wake", "schema": "public", "record": {"id": 9, "kind": kind, "ref": "x"}}, "ids": [1]}}))
                pong = await ws.ping(); await pong; SEEN["pongs"] += 1
            if CMD["close"]:
                CMD["close"] = False; await ws.close(); return
            try:
                m = json.loads(await asyncio.wait_for(ws.recv(), 0.05))
                if m.get("event") == "heartbeat":
                    SEEN["beats"] += 1
            except asyncio.TimeoutError:
                pass
    except websockets.ConnectionClosed:
        return

PORT = {}
def run_server():
    async def main():
        async with websockets.serve(handler, "127.0.0.1", 0) as srv:
            PORT["p"] = srv.sockets[0].getsockname()[1]
            await asyncio.Future()
    asyncio.run(main())
threading.Thread(target=run_server, daemon=True).start()
while "p" not in PORT: time.sleep(0.05)

w = realtime.Waker(f"http://127.0.0.1:{PORT['p']}", "svc-key-123")
w.HEARTBEAT_S = 1; w.RETRY_S = 0.3
stop = threading.Event()
threading.Thread(target=w.loop, args=(stop,), daemon=True).start()
t0 = time.time()
while not w.connected and time.time() - t0 < 5: time.sleep(0.05)
ok(w.connected, "connects and subscribes to wake inserts")
j = SEEN["joins"][0]
pc = j["payload"]["config"]["postgres_changes"][0]
ok(SEEN["paths"][0].startswith("/realtime/v1/websocket?") and "vsn=1.0.0" in SEEN["paths"][0] and "apikey=svc-key-123" in SEEN["paths"][0]
   and j["event"] == "phx_join" and pc == {"event": "INSERT", "schema": "public", "table": "wake"}, "the join is what Supabase Realtime expects")
w.wait("job", 0.01); w.wait("ping", 0.01)          # take the "look once now" wake-up
hits = []
def lane(): hits.append(w.wait("job", 5))
th = threading.Thread(target=lane); th.start(); time.sleep(0.2)
t1 = time.time(); CMD["push"].append("job"); th.join(6)
ok(hits == [True] and time.time() - t1 < 2, "a new job wakes a waiting lane at once")
ok(not w.wait("ping", 0.3), "a job event does not wake the ping loop")
CMD["push"].append("ping"); ok(w.wait("ping", 3), "Connect on the website wakes the ping loop")
time.sleep(0.5); ok(SEEN["pongs"] >= 2, "the server's pings are answered")
time.sleep(1.6); ok(SEEN["beats"] >= 1, "a heartbeat keeps the connection alive")
CMD["close"] = True; t0 = time.time()
while w.connected and time.time() - t0 < 3: time.sleep(0.05)
ok(not w.connected, "a dropped connection is noticed (the loops go back to polling)")
t0 = time.time()
while not w.connected and time.time() - t0 < 5: time.sleep(0.05)
ok(w.connected and SEEN["conns"] >= 2, "and it reconnects by itself")
stop.set()

# ---------------------------------------------------------------- the live-reel mirror
def iso(m): return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - m * 60)) + ".000000+00:00"
class DB:
    def __init__(self): self.jobs = {}; self.q = []
    def select(self, t, q=""):
        self.q.append((t, q))
        rows = list(self.jobs.values())
        for part in q.split("&"):
            k, _, v = part.partition("=")
            if k == "kind": rows = [r for r in rows if r["kind"] == v[3:]]
            if k == "status" and v.startswith("in.("): rows = [r for r in rows if r["status"] in v[4:-1].split(",")]
            if k == "id" and v.startswith("in.("): rows = [r for r in rows if r["id"] in v[4:-1].split(",")]
            if k == "updated_at" and v.startswith("gt."): cur = urllib.request.unquote(v[3:]); rows = [r for r in rows if r["updated_at"] > cur]
            if k == "updated_at" and v.startswith("gte."): rows = [r for r in rows if r["updated_at"] >= v[4:]]
        return pgsel.project([dict(r) for r in rows], q)
def job(i, status, upd=1, kind="reel"):
    return {"id": i, "kind": kind, "brand": "weone", "status": status, "stage": None, "progress": 0, "input": {"concept": {"title": i}},
            "output": {"script": "x" * 2000}, "error": None, "not_before": None, "created_at": iso(100 - len(i)), "updated_at": iso(upd),
            "attempts": 0, "parent_id": None, "lease_owner": None, "lease_expires_at": None}
db = DB()
for j in [job("r1", "running"), job("r2", "review"), job("r3", "done"), job("r4", "failed", 60), job("r5", "failed", 13 * 60), job("s1", "queued", kind="suggest")]:
    db.jobs[j["id"]] = j
class W:                                     # a connected Waker stand-in
    connected = True; gen = {"job": 0}
wk = W(); m = macapi.Mirror(db, wk)
m.sync(); live, stopped = m.view()
ok([r["id"] for r in live] == ["r1", "r2"] and [r["id"] for r in stopped] == ["r4"], "first look: live reels and failures of the last 12 h, as the website reads them")
n = len(db.q)
m.on_write("jobs", "id=eq.r1", {"progress": 0.5, "stage": "voice"}, [{"id": "r1", "updated_at": iso(0)}])
db.jobs["r1"].update(progress=0.5, stage="voice", updated_at=iso(0))
m.sync(); live, _ = m.view()
ok(len(db.q) == n and live[0]["progress"] == 0.5 and live[0]["stage"] == "voice", "the Mac's own progress save shows at once, with no read")
wk.gen["job"] += 1; m.sync()
ok(len(db.q) == n + 1 and "select=id,status,updated_at" in db.q[-1][1], "a wake-up costs one tiny read; its own save is not read again")
db.jobs["r2"].update(status="done", updated_at=iso(-0.1))
db.jobs["n1"] = job("n1", "queued", -0.1)
wk.gen["job"] += 1; m.sync(); live, _ = m.view()
ok([r["id"] for r in live] == ["r1", "n1"] or sorted(r["id"] for r in live) == ["n1", "r1"], "approved reel leaves, a new one appears")
ok(any("id=in.(" in q for _, q in db.q[-2:]) and not any("select=" + macapi.COLS in q and "status=in." in q for _, q in db.q[n + 1:]),
   "only the changed reels are read in full")
n = len(db.q); m.sync()
ok(len(db.q) == n, "nothing new and pushes connected: no read at all")
wk.connected = False; m.delta_at -= 6; m.sync()
ok(len(db.q) == n + 1, "without pushes it checks for changes at most every 5 s")

# ---------------------------------------------------------------- sign-in checks
class R:
    def __init__(self, j): self._j = j
    def json(self): return self._j
class ADB:
    def __init__(self): self.calls = 0; self.m = 0
    def _req(self, method, path, retries=3, headers=None):
        self.calls += 1
        tok = headers["Authorization"].split(" ", 1)[1]
        if "bad" in tok: raise RuntimeError("401")
        return R({"email": "Owner@Test.in" if "own" in tok else "stranger@x.in"})
    def select(self, t, q=""): self.m += 1; return [{"email": "owner@test.in"}]
import base64
def tok(tag, exp=3600):
    body = base64.urlsafe_b64encode(json.dumps({"exp": int(time.time() + exp)}).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJIUzI1NiJ9.{body}.{tag}signaturesignature"
adb = ADB(); a = macapi.Auth(adb)
ok(a.check("Bearer " + tok("own")) and adb.calls == 1, "a member's sign-in is accepted")
ok(a.check("Bearer " + tok("own")) and adb.calls == 1, "and remembered (no second check with Supabase)")
ok(not a.check("Bearer " + tok("other")), "someone who is not on the members list is refused")
ok(not a.check("Bearer " + tok("bad")), "a sign-in Supabase rejects is refused")
c0 = adb.calls; ok(not a.check("Bearer " + tok("own", -10)) and adb.calls == c0, "an expired sign-in is refused without asking")
ok(not a.check("") and not a.check("Basic abc"), "no sign-in, no data")

# ---------------------------------------------------------------- the local API
class FA:
    def check(self, h): return h == "Bearer good"
os.environ["REELSTUDIO_API_ORIGINS"] = "http://127.0.0.1:9999"
srv_stop = threading.Event(); port = 18789
threading.Thread(target=macapi.serve, args=(m, FA(), lambda: {"id": "mac-mini", "last_seen": iso(0), "info": {"realtime": True}}, port, srv_stop), daemon=True).start()
time.sleep(0.4)
def get(path, headers=None, method="GET"):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers or {}, method=method)
    try:
        r = urllib.request.urlopen(req); return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
s, h, b = get("/v1/health"); ok(s == 200 and json.loads(b)["ok"], "health answers without a sign-in (no data)")
s, h, b = get("/v1/poll"); ok(s == 401, "the live view needs a sign-in")
s, h, b = get("/v1/poll", {"Authorization": "Bearer good", "Origin": "http://127.0.0.1:9999"})
body = json.loads(b)
ok(s == 200 and {"live", "stopped", "worker", "at"} <= set(body) and body["worker"]["id"] == "mac-mini" and body["live"][0]["output"]["script"],
   "a member gets live reels, stopped reels and the Mac's status")
ok(h.get("Access-Control-Allow-Origin") == "http://127.0.0.1:9999" and h.get("Cache-Control") == "no-store", "the studio's address may read it; nothing is cached")
s, h, b = get("/v1/poll", {"Origin": "https://evil.example"}, "OPTIONS")
ok("Access-Control-Allow-Origin" not in h, "other websites are not allowed")
s, h, b = get("/v1/poll", {"Origin": "http://127.0.0.1:9999", "Access-Control-Request-Headers": "authorization"}, "OPTIONS")
ok(s == 204 and "authorization" in h.get("Access-Control-Allow-Headers", ""), "the browser's pre-check is answered")
s, h, b = get("/v1/other"); ok(s == 404, "nothing else is served")
srv_stop.set()

# ---------------------------------------------------------------- the tunnel
tmp = Path(tempfile.mkdtemp())
fake = tmp / "cloudflared"
fake.write_text("#!/bin/sh\necho 'INF Requesting new quick Tunnel on trycloudflare.com...'\necho '|  https://brave-otter-mac.trycloudflare.com  |'\nexec sleep 300\n")
fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
class SDB:
    def __init__(self): self.saved = []
    def insert(self, t, row, upsert=False, minimal=False): self.saved.append((t, row))
sdb = SDB(); tun = macapi.Tunnel(sdb, 18790, tmp, binary=str(fake), health=lambda u: True)
ts = threading.Event(); th = threading.Thread(target=tun.loop, args=(ts,), daemon=True); th.start()
t0 = time.time()
while tun.status != "online" and time.time() - t0 < 10: time.sleep(0.1)
ok(tun.url == "https://brave-otter-mac.trycloudflare.com" and sdb.saved and sdb.saved[-1][1] == {"key": "mac_api", "value": {"url": tun.url, "at": sdb.saved[-1][1]["value"]["at"]}},
   "the tunnel's address is published for the website")
ok((tmp / "cloudflared_home").exists() and (tmp / "cloudflared.pid").exists(), "it runs apart from any tunnel of your own, and its process is remembered")
pid = tun.proc.pid; ts.set(); th.join(15)
ok(tun.proc.poll() is not None and sdb.saved[-1][1]["value"]["url"] is None, "stopping the Mac stops the tunnel and tells the website to use Supabase")
t2 = macapi.Tunnel(sdb, 1, tmp, binary=None); t2.binary = None; t2.loop(threading.Event())
ok("not installed" in t2.status, "no cloudflared on the Mac: the website simply keeps reading Supabase")

bad = [n for c, n in res if not c]
print(f"{len(res) - len(bad)}/{len(res)} mac-first checks passed" + (" | FAILED: " + "; ".join(bad) if bad else ""))
sys.exit(1 if bad else 0)

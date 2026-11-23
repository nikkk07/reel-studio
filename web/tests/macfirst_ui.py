"""Mac first in the browser: with the Mac's API up, Production reads nothing from Supabase; when the Mac goes away the site
falls back to Supabase by itself. Local stack (mock :8765 -> PostgREST, real SQL) plus the real Mac API from the worker.
    python macfirst_ui.py"""
import json, os, subprocess, sys, threading, time, uuid
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "worker"))
os.environ["REELSTUDIO_API_ORIGINS"] = "http://127.0.0.1:8765"
from reelworker import macapi  # noqa: E402
from reelworker.db import Supabase  # noqa: E402

URL = "http://127.0.0.1:8765/index.html"
def sql(q): return subprocess.run(["psql", "-h", "/tmp", "-p", "5433", "-U", "postgres", "-d", "rs_it", "-tAc", q], capture_output=True, text=True).stdout.strip()
res = []
def ok(c, n): res.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n)

PORT = 18800
db = Supabase(url="http://127.0.0.1:8765", key=Path("/tmp/jwt_service").read_text().strip())
mirror = macapi.Mirror(db, None); db.on_job_write = mirror.on_write
stop = threading.Event()
row = lambda: {"id": "mac-mini", "last_seen": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "status": "idle", "version": "test",
               "claude_ok": True, "claude_note": "ok", "claude_checked_at": None, "info": {"reels_today": 0, "daily_cap": 3, "realtime": False}}
threading.Thread(target=macapi.serve, args=(mirror, macapi.Auth(db), row, PORT, stop), daemon=True).start()
time.sleep(0.5)
sql(f"insert into settings(key, value) values ('mac_api', '{{\"url\": \"http://127.0.0.1:{PORT}\"}}') on conflict (key) do update set value = excluded.value")
rid = str(uuid.uuid4())

with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    pg = b.new_page(viewport={"width": 1200, "height": 900}); errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    R = []
    pg.on("request", lambda r: R.append(r.url) if ("/rest/v1/jobs" in r.url or "/rest/v1/workers" in r.url or "/v1/poll" in r.url) else None)
    pg.goto(URL); pg.fill("input[type=email]", "weoneaviation8@gmail.com"); pg.fill("input[type=password]", "secret"); pg.click("button[type=submit]")
    pg.wait_for_timeout(3000)
    pg.goto(URL + "#/production"); pg.wait_for_timeout(2500); R.clear()
    sql(f"insert into jobs(id, kind, brand, status, input, output) values ('{rid}', 'reel', 'weone', 'review', '{{\"concept\":{{\"title\":\"Mac served reel\"}}}}', '{{\"title\":\"Mac served reel\"}}')")
    pg.mouse.move(5, 5); pg.mouse.down(); pg.mouse.up()
    try: pg.wait_for_selector("text=Mac served reel", timeout=20000); seen = True
    except Exception: seen = False
    ok(seen, "a new reel shows up in Production, served by the Mac")
    pg.wait_for_timeout(12000)
    mac = [u for u in R if "/v1/poll" in u]; sb = [u for u in R if "/rest/v1/jobs" in u or "/rest/v1/workers" in u]
    ok(len(mac) >= 2 and not sb, f"Production reads only from the Mac ({len(mac)} Mac reads, {len(sb)} Supabase reads)")
    lamps = pg.locator(".top .status .lamp").first.get_attribute("class") or ""
    ok("ok" in lamps, "the Mac lamp is green from the Mac's own status")
    stop.set(); time.sleep(1.5); R.clear()
    pg.mouse.move(6, 6); pg.mouse.down(); pg.mouse.up(); pg.wait_for_timeout(14000)
    sb = [u for u in R if "/rest/v1/jobs" in u]
    ok(sb and "Mac served reel" in pg.inner_text("main"), "the Mac goes away: Supabase takes over, the reel stays on screen")
    R.clear(); pg.wait_for_timeout(13000)
    ok(not [u for u in R if "/v1/poll" in u], "and the Mac is not asked again for a while")
    ok(not errors, f"no page errors {errors[:3]}")
    b.close()

sql(f"delete from jobs where id = '{rid}'"); sql("delete from settings where key = 'mac_api'")
bad = [n for c, n in res if not c]
print(f"{len(res) - len(bad)}/{len(res)} mac-first UI checks passed" + (" | FAILED: " + "; ".join(bad) if bad else ""))

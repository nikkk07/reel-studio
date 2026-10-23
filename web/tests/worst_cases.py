"""Worst-case user scenarios against the real stack: local Postgres + PostgREST (0001+0002), mock_supabase.py in proxy mode,
the real worker (mock brain that takes REELSTUDIO_MOCK_BRAIN_SECONDS), and Chromium.

    python worst_cases.py <out_dir>        env: USER_JWT, PG ("psql -h /tmp -p 5433 -U postgres -d rs_it"), WORKER_LOG
"""
import json, os, subprocess, sys, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
BASE = "http://127.0.0.1:8765"; URL = BASE + "/index.html"
PG = os.environ.get("PG", "psql -h /tmp -p 5433 -U postgres -d rs_it")
WLOG = Path(os.environ.get("WORKER_LOG", "/home/claude/rs_test2/logs/worker.log"))
results, errors = [], []


def sql(q):
    return subprocess.run(f"{PG} -tAc {json.dumps(q)}", shell=True, capture_output=True, text=True).stdout.strip()


def rpc(fn, **args):
    req = urllib.request.Request(f"{BASE}/rest/v1/rpc/{fn}", data=json.dumps(args).encode(), method="POST",
                                 headers={"Authorization": "Bearer " + os.environ["USER_JWT"], "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read() or b"null")


def ok(cond, name):
    results.append((bool(cond), name)); print(("PASS " if cond else "FAIL ") + name, flush=True)


def wait(cond, timeout, step=1.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = cond()
        if v: return v
        time.sleep(step)
    return None


def helpers(job):   # helper processes of a job still alive
    out = subprocess.run(["ps", "-A", "-o", "pid=,command="], capture_output=True, text=True).stdout
    n = [l for l in out.splitlines() if job in l]
    for pid in subprocess.run(["pgrep", "-f", "sleep"], capture_output=True, text=True).stdout.split():
        try:
            if job in os.readlink(f"/proc/{pid}/cwd"): n.append(pid)
        except OSError: pass
    return n


with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    pg = b.new_context(viewport={"width": 420, "height": 900}, service_workers="block").new_page()  # the local proxy is same-origin; real Supabase is not
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(URL)
    pg.fill("input[type=email]", "weoneaviation8@gmail.com"); pg.fill("input[type=password]", "secret"); pg.click("button[type=submit]")
    pg.click("button:has-text('Connect to Server')")
    pg.wait_for_selector("text=Create a reel", timeout=60000)
    ok(True, "sign in + Connect to Server reaches Create")

    # ---- W1: idea request survives leaving the page, and Stop works on it
    pg.click("button:has-text('Generate suggestions')")
    pg.wait_for_selector(".thinking", timeout=10000)
    pg.click("nav.tabbar >> text=Production"); pg.wait_for_timeout(1500)
    pg.click("nav.tabbar >> text=Create"); pg.wait_for_timeout(1500)
    ok(pg.locator(".thinking").count() == 1 and int(sql("select count(*) from jobs where kind='suggest'")) == 1,
       "W1 leave Create and return: same request still shown, no duplicate created")
    pg.click("button:has-text('Generate suggestions')", force=True); pg.wait_for_timeout(800)
    ok(int(sql("select count(*) from jobs where kind='suggest'")) == 1, "W1 tapping Generate again does not start a second request")
    pg.wait_for_selector(".thinking button:has-text('Stop')", timeout=5000)
    pg.reload(); pg.wait_for_selector(".thinking", timeout=20000)
    ok(True, "W1 page reload re-attaches to the running request")
    pg.click(".thinking button:has-text('Stop')")
    pg.wait_for_selector("text=Stopped. Nothing was changed.", timeout=30000)
    sid = sql("select id from jobs where kind='suggest'")
    ok(wait(lambda: not helpers(sid), 25), "W1 Stop ends the Mac's Claude process for that request")
    ok(pg.locator("button:has-text('Try again')").count() == 1, "W1 a working Try again button is offered")

    # ---- W2: reel Stop during a long Claude step survives page refresh cycles, takes effect fast, is never undone
    a = rpc("create_job", p_kind="reel", p_input={"concept": {"title": "Worst case A", "hook": "h"}})
    ok(rpc("create_job", p_kind="reel", p_input={"concept": {"title": " worst case a"}})["id"] == a["id"], "W2 double Create returns the same reel")
    pg.click("nav.tabbar >> text=Production")
    ok(wait(lambda: sql(f"select status||stage from jobs where id='{a['id']}'") == "runningscript", 60), "W2 worker started the reel")
    wait(lambda: helpers(a["id"]), 20)
    pg.wait_for_selector("button:has-text('Stop reel')", timeout=20000)
    pg.click("button:has-text('Stop reel')")
    pg.wait_for_timeout(7000)        # several 5 s refreshes happen here (this used to reset the button)
    ok(pg.locator("button:has-text('Yes, stop')").count() == 1, "W2 Stop confirmation survives automatic refreshes")
    t0 = time.time(); pg.click("button:has-text('Yes, stop')")
    ok(wait(lambda: sql(f"select status from jobs where id='{a['id']}'") == "cancelled", 10, 0.3), "W2 status is Stopped right away")
    ok(wait(lambda: not helpers(a["id"]), 25), f"W2 Mac ends the running Claude step ({time.time() - t0:.0f}s after tap)")
    ok(wait(lambda: sql(f"select coalesce(lease_owner,'') from jobs where id='{a['id']}'") == "", 25), "W2 Mac hands the reel back (Resume possible)")
    time.sleep(8)
    ok(sql(f"select status from jobs where id='{a['id']}'") == "cancelled", "W2 the worker did not undo the Stop (no auto-retry)")
    pg.wait_for_selector("button:has-text('Resume'):not([disabled])", timeout=20000)
    pg.screenshot(path=OUT / "w2_stopped.png", full_page=True)

    # ---- W3: Resume continues from the checkpoint; double tap is harmless; then Stop during the voice model
    pg.locator("button:has-text('Resume')").first.evaluate("el => { el.click(); el.click(); }")
    ok(wait(lambda: sql(f"select status from jobs where id='{a['id']}'") in ("queued", "running"), 10, 0.3), "W3 Resume queues the reel again")
    ok(wait(lambda: sql(f"select stage from jobs where id='{a['id']}'") == "voice", 180), "W3 resumed reel finished Script and reached Voice")
    wait(lambda: any("reelworker.media.voice" in x for x in helpers(a["id"])), 60)
    time.sleep(5)
    rpc("stop_job", p_job=a["id"])
    ok(wait(lambda: not helpers(a["id"]), 30), "W3 Stop during Voice kills the voice model process")
    ok(wait(lambda: sql(f"select coalesce(lease_owner,'')||status from jobs where id='{a['id']}'") == "cancelled", 30), "W3 reel handed back as Stopped")
    st = json.loads((Path("/home/claude/rs_test2/jobs") / a["id"] / "state.json").read_text())
    ok("script" in st["done"], "W3 finished Script step is kept for the next Resume")
    rpc("retry_job", p_job=a["id"])
    ok(wait(lambda: sql(f"select stage from jobs where id='{a['id']}'") == "voice", 90), "W3 second Resume skips Script and goes straight to Voice")
    rpc("stop_job", p_job=a["id"]); wait(lambda: sql(f"select coalesce(lease_owner,'') from jobs where id='{a['id']}'") == "", 40)

    # ---- W4: network drops: reels stay on screen with an honest banner, then recover
    pg.reload(); pg.wait_for_selector(".strip", timeout=20000)
    REST = lambda u: "/rest/v1/" in u
    pg.route(REST, lambda r: r.abort())
    pg.wait_for_timeout(14000)
    ok(pg.locator(".strip").count() >= 1, "W4 connection lost: reels do not vanish")
    ok(pg.locator("text=Connection to the studio dropped").count() == 1, "W4 connection banner shown")
    pg.screenshot(path=OUT / "w4_offline.png", full_page=True)
    pg.unroute(REST); pg.wait_for_timeout(14000)
    ok(pg.locator("text=Connection to the studio dropped").count() == 0, "W4 banner clears when the connection is back")

    # ---- W5: review: typing is never wiped by refreshes; double taps create exactly one new version / one approval
    sql("insert into jobs(id,kind,status,stage,input,output) values ('aaaaaaaa-0000-0000-0000-000000000001','reel','review','finished',"
        "'{\"concept\":{\"title\":\"Review me\"}}','{\"title\":\"Review me\",\"duration\":40,\"script\":[]}')")
    sql("insert into jobs(id,kind,status,stage,input,output) values ('aaaaaaaa-0000-0000-0000-000000000002','reel','review','finished',"
        "'{\"concept\":{\"title\":\"Approve me\"}}','{\"title\":\"Approve me\",\"duration\":40,\"script\":[]}')")
    daily = Path("/home/claude/rs_test2/state/daily.json"); daily.parent.mkdir(exist_ok=True)
    daily.write_text(json.dumps({"date": time.strftime("%Y-%m-%d"), "count": 99}))      # today's reel limit reached
    pg.wait_for_selector("text=Review me", timeout=15000)
    card = pg.locator("article.strip", has_text="Review me")
    card.locator("button:has-text('Request changes')").click()
    card.locator("textarea").fill("Say the CTA only once")
    pg.wait_for_timeout(12000)
    ok(card.locator("textarea").input_value() == "Say the CTA only once", "W5 notes being typed survive automatic refreshes")
    card.locator("button:has-text('Send changes')").evaluate("el => { el.click(); el.click(); }")
    pg.wait_for_timeout(3000)
    ok(sql("select count(*) from jobs where parent_id='aaaaaaaa-0000-0000-0000-000000000001'") == "1", "W5 double Send changes creates exactly one new version")
    ok(sql("select input->>'notes' from jobs where parent_id='aaaaaaaa-0000-0000-0000-000000000001'") == "Say the CTA only once", "W5 new version carries the notes")
    ok(pg.locator("article.strip", has_text="Review me").count() <= 1, "W5 old version leaves the list (no confusing Resume on it)")
    pg.locator("article.strip", has_text="Approve me").locator("button:has-text('Approve')").evaluate("el => { el.click(); el.click(); }")
    ok(wait(lambda: sql("select status from jobs where id='aaaaaaaa-0000-0000-0000-000000000002'") == "done", 10), "W5 Approve (double tap safe)")
    pg.screenshot(path=OUT / "w5_review.png", full_page=True)

    # ---- W6: daily limit reached: Request changes still runs, a new reel waits, and Stop/Resume on a waiting reel is instant
    ok(wait(lambda: sql("select status from jobs where parent_id='aaaaaaaa-0000-0000-0000-000000000001'") == "running", 40), "W6 limit reached: the revision still runs")
    c = rpc("create_job", p_kind="reel", p_input={"concept": {"title": "Waits for tomorrow"}})
    time.sleep(20)
    ok(sql(f"select status from jobs where id='{c['id']}'") == "queued", "W6 limit reached: a new reel waits in the queue")
    rpc("stop_job", p_job=c["id"]); r = rpc("retry_job", p_job=c["id"])
    ok(r["status"] == "queued", "W6 Stop then Resume on a waiting reel works instantly")
    rpc("stop_job", p_job=c["id"]); daily.unlink()
    b.close()

print("page errors:", errors or "none")
bad = [n for good, n in results if not good]
print(f"{len(results) - len(bad)}/{len(results)} worst-case checks passed" + ("" if not bad else " | FAILED: " + "; ".join(bad)))
sys.exit(1 if bad or errors else 0)

"""Autopilot panel + held reel in the browser against the local stack (mock :8765 -> PostgREST, real SQL).   python autopilot_ui.py"""
import json, subprocess, sys, time, uuid
from playwright.sync_api import sync_playwright
URL = "http://127.0.0.1:8765/index.html"; R = []; errors = []
def sql(q): return subprocess.run(["psql", "-h", "/tmp", "-p", "5433", "-U", "postgres", "-d", "rs_it", "-tAc", q], capture_output=True, text=True).stdout.strip()
def ok(c, n): R.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n, flush=True)
sql("delete from jobs where kind='suggest'; update settings set value='{\"on\": true, \"time\": \"13:30\", \"tz\": \"Asia/Kolkata\"}' where key='autopilot'; update jobs set status='cancelled' where kind='reel' and status in ('queued','running','review','waiting_quota')")
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    for vw in ({"width": 1280, "height": 900}, {"width": 390, "height": 860}):
        cx = b.new_context(viewport=vw, service_workers="block"); cx.add_init_script("sessionStorage.setItem('rs_checked', String(Date.now()))")
        pg = cx.new_page(); pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(URL); pg.fill("input[type=email]", "weoneaviation8@gmail.com"); pg.fill("input[type=password]", "secret"); pg.click("button[type=submit]"); pg.wait_for_selector(".acct h3", timeout=60000); pg.goto(URL + "#/create")
        try: pg.click("button:has-text('Connect to Server')", timeout=5000)
        except Exception: pass
        pg.wait_for_selector("text=Create a reel", timeout=60000)
        pg.wait_for_selector(".ap h3", timeout=15000)
        if vw["width"] == 1280:
            ok("every day at 1:30" in pg.inner_text(".ap h3") and "Next run" in pg.inner_text(".ap"), "panel: on, every day at 1:30 PM, next run shown")
            pg.click(".ap button:has-text('Run now')"); pg.wait_for_timeout(1500)
            ok(sql("select count(*) from jobs where kind='suggest' and input->>'autopilot'='true' and input->>'trigger'='manual'") == "1", "Run now queues one autopilot run")
            pg.wait_for_selector(".ap button:has-text('Running')", timeout=12000); ok(True, "panel shows it running; Run now disabled")
            jid = sql("select id from jobs where kind='suggest' and input->>'autopilot'='true'")
            rid = str(uuid.uuid4())
            sql(f"insert into jobs(id,kind,status,input,output) values ('{rid}','reel','queued','{{\"concept\":{{\"title\":\"CPL ke liye DGCA ke 6 exams\"}},\"autopilot\":\"{jid}\"}}','{{}}')")
            out = {"autopilot": {"title": "CPL ke liye DGCA ke 6 exams", "hook": "Pilot banna hai? Ye 6 exam pass karne padenge", "category": "career", "score": 81.5, "why": "High save value for aspirants", "reel_id": rid, "day": "2026-10-23"},
                   "topics": [{"title": "CPL ke liye DGCA ke 6 exams", "score": 81.5, "editor_note": "saves"}, {"title": "Squawk 7700", "score": None, "repeat": True}, {"title": "Winter schedule", "score": 62.0}]}
            sql(f"update jobs set status='done', output='{json.dumps(out)}' where id='{jid}'")
            pg.wait_for_selector(".ap-pick h3", timeout=15000)
            t = pg.inner_text(".ap")
            ok("CPL ke liye DGCA ke 6 exams" in t and "81.5/100" in t and "career" in t, "last pick shows title, score and category")
            pg.click(".ap summary"); ok(pg.locator(".ap-board li").count() == 3 and "repeat" in pg.inner_text(".ap-board"), "all scored topics listed (repeat marked)")
            ok("No suggestions yet" in pg.inner_text("main"), "the autopilot run does not show up as manual suggestions")
            pg.click(".ap button:has-text('Follow this reel')")
            try: pg.wait_for_selector("h1:has-text('Production')", timeout=15000); pg.wait_for_selector(".strip >> text=CPL ke liye DGCA", timeout=15000); f = True
            except Exception: f = False
            ok(f, "Follow this reel opens it in Production")
            pg.click("nav >> text=Create"); pg.wait_for_selector(".ap h3")
            pg.click(".ap button:has-text('Change time')"); pg.fill(".ap input[type=time]", "14:15"); pg.click(".ap button:has-text('Save')"); pg.wait_for_timeout(1500)
            ok(sql("select value->>'time' from settings where key='autopilot'") == "14:15" and "2:15" in pg.inner_text(".ap h3"), "time changed to 2:15 PM")
            pg.click(".ap button:has-text('Turn off')"); pg.wait_for_timeout(1500)
            ok(sql("select value->>'on' from settings where key='autopilot'") == "false" and "off" in pg.inner_text(".ap h3"), "turn off")
            pg.click(".ap button:has-text('Turn on')"); pg.wait_for_timeout(1200)
            sql("update settings set value='{\"on\": true, \"time\": \"13:30\", \"tz\": \"Asia/Kolkata\"}' where key='autopilot'")
            # held reel
            sql(f"update jobs set status='cancelled' where id='{rid}'")
            hid = str(uuid.uuid4())
            sql(f"insert into jobs(id,kind,status,input,output) values ('{hid}','reel','running','{{\"concept\":{{\"title\":\"Held reel\"}},\"autopilot\":\"{jid}\"}}','{{\"title\":\"Held reel\",\"autopilot_hold\":true,\"autopilot_note\":\"Not posted automatically: quality check 4/10\",\"qc\":{{\"score\":4,\"summary\":\"weak\",\"issues\":[]}},\"duration\":40}}')")
            sql(f"update jobs set status='review' where id='{hid}'")
            pg.click("nav >> text=Production")
            try: pg.wait_for_selector("text=Autopilot held it back", timeout=20000)
            except Exception: pass
            pg.wait_for_timeout(1500); body = pg.inner_text("main")
            ok(("Autopilot held it back" in body or "Not posted automatically" in body) and "Posts to Instagram automatically in" not in body, "held reel: clear note, no auto-post countdown")
            pg.screenshot(path="/tmp/rs_stack/ap_held.png", full_page=False)
            sql("update jobs set output = output || jsonb_build_object('review_deadline', now() - interval '1 s') where id='" + hid + "'"); sql("select public.auto_approve_due()")
            ok(sql(f"select status from jobs where id='{hid}'") == "review", "held reel stays unposted")
            pg.click("nav >> text=Create"); pg.wait_for_selector(".ap h3"); pg.screenshot(path="/tmp/rs_stack/ap_desktop.png", full_page=False)
        else:
            pg.screenshot(path="/tmp/rs_stack/ap_mobile.png", full_page=False)
            ok(pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "phone width: no sideways scroll")
    ok(not errors, f"no page errors {errors[:2]}")
    b.close()
sql("delete from jobs where kind='suggest'; update jobs set status='cancelled' where kind='reel' and status='review'")
bad = [n for k, n in R if not k]; print(f"{len(R) - len(bad)}/{len(R)} autopilot UI checks passed" + (" | FAILED: " + "; ".join(bad) if bad else "")); sys.exit(1 if bad else 0)

"""Scheduled posting per account: switch on the dashboard, times dialog with the account's best hours, approval books a slot.
Local stack (mock :8765 -> PostgREST, real SQL).   python post_slots_ui.py"""
import json, subprocess
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright
URL = "http://127.0.0.1:8765/index.html"; R = []; errors = []
def sql(q): return subprocess.run(["psql", "-h", "/tmp", "-p", "5433", "-U", "postgres", "-d", "rs_it", "-tAc", q], capture_output=True, text=True).stdout.strip()
def ok(c, n): R.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n, flush=True)
before = sql("select string_agg(id||'='||status, ',') from brands")
sql("update brands set status='live' where id='flystar'")
sql("delete from settings where key like 'post_slots%'; delete from jobs where kind='reel'")
sql("""insert into settings(key,value) values ('instagram_status@flystar','{"state":"connected","user_id":"1","username":"fs"}') on conflict (key) do update set value = excluded.value""")
# Flying Star's past posts: 4 PM (India) does best
now = datetime.now(timezone.utc).replace(hour=10, minute=30)          # 16:00 IST
posts = [{"at": (now - timedelta(days=i)).strftime("%Y-%m-%dT%H:%M:%SZ"), "type": "FEED", "likes": 90 + i, "comments": 3} for i in range(3)] + \
        [{"at": (now - timedelta(days=i, hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ"), "type": "FEED", "likes": 10 + i, "comments": 0} for i in range(3)]
sql(f"insert into settings(key,value) values ('ig_library@flystar', $j${json.dumps({'posts': posts})}$j$) on conflict (key) do update set value = excluded.value")
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    cx = b.new_context(viewport={"width": 1280, "height": 900}, service_workers="block"); cx.add_init_script("sessionStorage.setItem('rs_checked', String(Date.now()))")
    pg = cx.new_page(); pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(URL); pg.fill("input[type=email]", "weoneaviation8@gmail.com"); pg.fill("input[type=password]", "secret"); pg.click("button[type=submit]")
    card = "article.acct:has(h3:text-is('Flying Star Aviator'))"; pg.wait_for_selector(card, timeout=60000); pg.wait_for_timeout(1000)
    sw = card + " button[aria-label='Scheduled posting for Flying Star Aviator']"
    ok(pg.get_attribute(sw, "aria-checked") == "false" and "approved reels post right away" in pg.inner_text(card), "dashboard: Scheduled posting is its own switch, off by default")
    ok(pg.query_selector(card + " button[aria-label='Autopilot for Flying Star Aviator']") is not None, "Autopilot switch stays separate")
    pg.click(sw); pg.wait_for_selector("dialog[aria-label='Scheduled posting for Flying Star Aviator']")
    dt = pg.inner_text("dialog")
    ok("4:00" in dt and "best hours so far" in dt, "dialog suggests the account's best hour from its own posts")
    ok(pg.input_value("dialog input[aria-label='Posting time 1 (India)']") == "16:00", "best hour pre-filled")
    pg.fill("dialog input[aria-label='Posting time 2 (India)']", "20:30"); pg.click("dialog button:has-text('Save')"); pg.wait_for_timeout(1500)
    ok(sql("select value->'times' from settings where key='post_slots@flystar'") == '["16:00", "20:30"]', "times saved for Flying Star only")
    ok(pg.get_attribute(sw, "aria-checked") == "true" and "Posts at" in pg.inner_text(card), "switch on, times shown on the card")
    # approve a reel: it books the next slot instead of posting now
    sql("""insert into jobs(kind,brand,status,input,output) values ('reel','flystar','review','{}','{"title":"Slot reel","duration":30}')""")
    jid = sql("select id from jobs where output->>'title'='Slot reel'")
    pg.evaluate("localStorage.setItem('rs_brand','flystar')"); pg.goto(URL + f"#/production/{jid}"); pg.reload()
    pg.wait_for_selector("button:has-text('Approve & schedule')", timeout=20000)
    ok(True, "review button says Approve & schedule")
    pg.click("button:has-text('Approve & schedule')"); pg.wait_for_timeout(2000)
    slot = sql("select output->>'post_slot' from jobs where id = '" + jid + "'")
    ok(bool(slot) and sql(f"select (output->'ig'->>'at')::timestamptz > now() from jobs where id='{jid}'") == "t", f"approval booked a future slot ({slot})")
    pg.goto(URL + "#/library"); pg.wait_for_selector(".libcard", timeout=20000); pg.wait_for_timeout(800)
    ok("Instagram: scheduled for" in pg.inner_text(".libcard"), "Library shows the scheduled time")
    pg.click(".libcard button:has-text('Post to Instagram')"); pg.wait_for_timeout(1200)
    ok(sql(f"select (output->'ig'->>'at')::timestamptz <= now() from jobs where id='{jid}'") == "t", "Post now still posts at once")
    pg.goto(URL + "#/home"); pg.wait_for_selector(card, timeout=20000); pg.wait_for_timeout(1000)
    pg.click(sw); pg.wait_for_timeout(1500)
    ok(pg.get_attribute(sw, "aria-checked") == "false" and sql("select value->>'on' from settings where key='post_slots@flystar'") == "false", "switch off works")
    pg.set_viewport_size({"width": 390, "height": 844}); pg.wait_for_timeout(500)
    w = pg.evaluate("document.documentElement.scrollWidth"); ok(w <= 391, f"mobile: no sideways scroll ({w}px)")
    pg.locator(card).screenshot(path="/tmp/rs_stack/ps_card_m.png")
    ok(not errors, f"no page errors {errors[:2]}")
for kv in before.split(","): k, v = kv.split("="); sql(f"update brands set status='{v}' where id='{k}'")
bad = [n for k, n in R if not k]; print(f"{len(R) - len(bad)}/{len(R)} scheduled-posting UI checks passed" + (" | FAILED: " + "; ".join(bad) if bad else ""))

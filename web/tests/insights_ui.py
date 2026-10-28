"""Performance dashboards: home section, per-account Insights, weekly digest. Local stack (mock :8765 -> PostgREST).   python insights_ui.py"""
import json, subprocess
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright
URL = "http://127.0.0.1:8765/index.html"; R = []; errors = []
def sql(q): return subprocess.run(["psql", "-h", "/tmp", "-p", "5433", "-U", "postgres", "-d", "rs_it", "-tAc", q], capture_output=True, text=True)
def ok(c, n): R.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n, flush=True)
now = datetime.now(timezone.utc); iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
before = sql("select string_agg(id||'='||status, ',') from brands").stdout.strip()
sql("update brands set status='live' where id in ('weone','bookmycharter','flystar','legalgroup')")
sql("delete from jobs where kind='reel'; delete from dm_log; delete from settings where key like 'digest%' or key like 'ig_library%'")
rows = []
for i in range(9):          # We One: 9 posted reels, one strong
    v = 5200 if i == 6 else 300 + i * 40; t = now - timedelta(days=i * 1.5 + 0.3)
    rows.append(("weone", t, {"title": f"We One reel {i}", "ig": {"state": "posted", "posted_at": iso(t), "permalink": f"https://www.instagram.com/reel/x{i}/"},
                 "ig_stats": {"views": v, "reach": int(v * .8), "shares": i, "saved": i // 2, "reels_skip_rate": 40 + i * 3}, "duration": 38,
                 "hook": {"say": f"Opening line {i}", "text": f"HOOK {i}"}}))
for i in range(3):          # Book My Charter: 3 posted, watch-time only
    t = now - timedelta(days=i * 3 + 1)
    rows.append(("bookmycharter", t, {"title": f"BMC reel {i}", "ig": {"state": "posted", "posted_at": iso(t)}, "ig_stats": {"views": 900 + i * 100, "reach": 700, "shares": 2, "saved": 1, "avg_watch_s": 9.5}, "duration": 43}))
rows.append(("weone", now - timedelta(hours=3), {"title": "Waiting for review"}))
for b, t, o in rows:
    r = sql(f"insert into jobs(kind,brand,status,input,output,created_at) values ('reel','{b}','done','{{}}', $j${json.dumps(o)}$j$, '{iso(t)}')")
    if r.returncode: print(r.stderr); raise SystemExit(1)
dg = {"week_start": iso(now - timedelta(days=7)), "at": iso(now - timedelta(hours=2)), "numbers": {"made": 5, "posted": 4},
      "best": {"title": "We One reel 6", "views": 5200, "hold": 42, "shares": 6, "saved": 3, "opening": "Opening line 6", "permalink": "https://www.instagram.com/reel/x6/"},
      "worst": {"title": "We One reel 1", "views": 340, "hold": 57}, "lessons": [{"agent": "scriptwriter", "lesson": "Lead with the number, not the setup.", "status": "trial"}],
      "insights": "Reels opening on a number held viewers longer.", "next_experiment": "Open the next 3 reels with a number in the first line.", "evidence": "weak", "measured": 4}
sql(f"insert into settings(key,value) values ('digest', $j${json.dumps(dg)}$j$)")
sql(f"insert into settings(key,value) values ('ig_library', $j${json.dumps({'posts': [{'type': 'REELS', 'likes': 20}, {'type': 'FEED', 'likes': 45}, {'type': 'FEED', 'likes': 55}]})}$j$)")
for i in range(3): sql(f"insert into dm_log(brand,comment_id,media_id,username,keyword,text,state,created_at) values ('weone','c{i}','m','u{i}','PILOT','hi','sent','{iso(now - timedelta(days=i))}')")
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    for vw, tag in (((1280, 900), "desktop"), ((390, 844), "mobile")):
        cx = b.new_context(viewport={"width": vw[0], "height": vw[1]}, service_workers="block"); cx.add_init_script("sessionStorage.setItem('rs_checked', String(Date.now()))")
        pg = cx.new_page(); pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(URL); pg.fill("input[type=email]", "weoneaviation8@gmail.com"); pg.fill("input[type=password]", "secret"); pg.click("button[type=submit]")
        pg.wait_for_selector(".perf .kpis", timeout=60000); pg.wait_for_timeout(800)
        k = pg.inner_text(".perf .kpis")
        if tag == "desktop":
            ok("Views" in k and "DMs sent" in k and "vs last week" in k, "home: KPI tiles with week-over-week change")
            bars = pg.query_selector_all(".perf .hbar")
            ok(len(bars) >= 8, f"home: views and hold bars per live account ({len(bars)})")
            ok("We One Aviation" in pg.inner_text(".perf") and "Book My Charter" in pg.inner_text(".perf"), "home: every bar is labelled with its account")
            ok("Open the next 3 reels" in pg.inner_text(".dg-grid") and "Early signal" in pg.inner_text(".dg-grid"), "home: digest card shows next experiment and the weak-evidence note")
            pg.hover(".perf .hbar"); pg.wait_for_timeout(200); ok(pg.is_visible(".vtip"), "hover shows a tooltip")
            pg.click(".dg-card:has-text('We One Aviation')"); pg.wait_for_selector(".digest .rtile", timeout=20000); pg.wait_for_timeout(600)
            t = pg.inner_text("main")
            ok("Best reel" in t and "Weakest reel" in t and "Lead with the number" in t and "Next experiment" in t, "Insights: digest with best/worst, lessons, next experiment")
            cols = pg.query_selector_all("svg.vchart g.hit path"); ok(len(cols) == 9, f"Insights: one column per posted reel ({len(cols)})")
            ok(len(pg.query_selector_all("svg.vchart text.val")) == 1 and "5.2K" in (pg.text_content("svg.vchart") or ""), "Insights: only the tallest column carries its value")
            ok("HOOK 0" in pg.inner_text("main") and "%" in pg.inner_text(".hbars"), "Insights: hook bank ranks openings by hold")
            ok("Photos & carousels" in t, "Insights: the account's other posts are compared")
            pg.click("summary:has-text('Table view')"); ok(pg.is_visible(".dtable") and len(pg.query_selector_all(".dtable tbody tr")) == 9, "Insights: table view of the same numbers")
            pg.screenshot(path="/tmp/rs_stack/insights_d.png", full_page=True)
            pg.goto(URL + "#/home"); pg.wait_for_selector(".perf .kpis", timeout=30000)
            pg.click(".dg-card:has-text('Book My Charter')"); pg.wait_for_selector("main h1:text-is('Insights')", timeout=20000); pg.wait_for_timeout(1200)
            ok("first digest appears" in pg.inner_text("main"), "Insights without a digest says when it appears")
        else:
            sw = pg.evaluate("document.documentElement.scrollWidth"); ok(sw <= 391, f"mobile home: no sideways scroll ({sw}px)")
            pg.screenshot(path="/tmp/rs_stack/perf_m.png", full_page=True)
            pg.goto(URL + "#/insights"); pg.wait_for_selector(".digest", timeout=20000); pg.wait_for_timeout(800)
            sw = pg.evaluate("document.documentElement.scrollWidth"); ok(sw <= 391, f"mobile Insights: no sideways scroll ({sw}px)")
            pg.screenshot(path="/tmp/rs_stack/insights_m.png", full_page=True)
        cx.close()
    ok(not errors, f"no page errors {errors[:2]}")
for kv in before.split(","): k, v = kv.split("="); sql(f"update brands set status='{v}' where id='{k}'")   # leave the stack as found
bad = [n for k, n in R if not k]; print(f"{len(R) - len(bad)}/{len(R)} insights UI checks passed" + (" | FAILED: " + "; ".join(bad) if bad else ""))

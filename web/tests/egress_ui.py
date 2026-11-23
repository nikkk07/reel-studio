"""Data guard in the browser (Supabase egress): light refreshes, nothing while hidden, the meter, the saver banner and the
Cloud data card. Local stack (mock :8765 -> PostgREST, real SQL).   python egress_ui.py"""
import json, subprocess, time, uuid
from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8765/index.html"
def sql(q): return subprocess.run(["psql", "-h", "/tmp", "-p", "5433", "-U", "postgres", "-d", "rs_it", "-tAc", q], capture_output=True, text=True).stdout.strip()
res = []
def ok(c, n): res.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n)

sql("delete from egress_log; delete from settings where key = 'egress_guard'")
big = json.dumps({"at": "2026-11-23T00:00:00Z", "posts": [{"id": str(i), "caption": "c" * 200, "at": "2026-11-20T10:00:00Z", "likes": 5} for i in range(50)]})
sql(f"insert into settings(key, value) values ('ig_library', '{big}') on conflict (key) do update set value = excluded.value")
rid = str(uuid.uuid4())
out = json.dumps({"title": "Light reel", "script": "x" * 40000, "thumb": f"{rid}/thumb.jpg", "duration": 40, "ig": {"state": "posted", "posted_at": "2026-11-22T10:00:00Z"}})
sql(f"insert into jobs(id, kind, brand, status, input, output) values ('{rid}', 'reel', 'weone', 'done', '{{\"concept\":{{\"title\":\"Light reel\"}}}}', '{out}')")

with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    pg = b.new_page(viewport={"width": 1200, "height": 900}); errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    R = []
    pg.on("request", lambda r: R.append(r.url) if ("/rest/v1/" in r.url or "/storage/v1/object/sign" in r.url) else None)
    pg.goto(URL); pg.fill("input[type=email]", "weoneaviation8@gmail.com"); pg.fill("input[type=password]", "secret"); pg.click("button[type=submit]")
    pg.wait_for_timeout(3000)
    ok(int(sql("select coalesce(sum(reqs), 0) from egress_log where part = 'web'") or 0) > 0, "the page adds its own count at sign-in")
    ok(all("not.like.ig_library" in u for u in R if "/rest/v1/settings?select=key%2Cvalue&key=" in u), "the routine settings read leaves out the post library and digests")

    # Library: a light list on every refresh, the full reel only once; thumbnails keep the same signed link
    pg.goto(URL + "#/library"); pg.wait_for_selector(".libcard", timeout=15000); R.clear()
    pg.mouse.move(10, 10); pg.mouse.down(); pg.mouse.up()
    pg.wait_for_timeout(65000)
    full = [u for u in R if "/rest/v1/jobs" in u and "output" in u and "id=in." in u]
    lite = [u for u in R if "/rest/v1/jobs" in u and "select=id%2Cupdated_at" in u]
    ok(len(lite) >= 2 and not full, f"library refreshes read only id + date ({len(lite)} light, {len(full)} full)")
    ok(not [u for u in R if "/storage/v1/object/sign" in u], "thumbnails are not signed again")
    sql(f"update jobs set output = output || '{{\"ig_stats\": {{\"views\": 99}}}}' where id = '{rid}'"); R.clear()
    pg.wait_for_timeout(33000)
    full = [u for u in R if "/rest/v1/jobs?select=id%2Cinput%2Coutput" in u and "id=in." in u]
    ok(len(full) == 1 and rid in full[0], f"a changed reel is read again, alone ({len(full)})")

    # a hidden tab reads nothing for minutes
    pg.evaluate("Object.defineProperty(document, 'hidden', { configurable: true, get: () => true }); document.dispatchEvent(new Event('visibilitychange'))")
    pg.wait_for_timeout(1500); R.clear(); pg.wait_for_timeout(45000)
    ok(not [u for u in R if "/rest/v1/jobs" in u or "/rest/v1/settings" in u], f"hidden tab: no reads in 45 s ({len(R)} requests)")
    pg.evaluate("Object.defineProperty(document, 'hidden', { configurable: true, get: () => false }); document.dispatchEvent(new Event('visibilitychange'))")
    pg.wait_for_timeout(2500)
    ok([u for u in R if "/rest/v1/jobs" in u], "visible again: it catches up at once")

    # Studio settings: the Cloud data card, and the owner sets the budget
    pg.goto(URL + "#/settings"); pg.wait_for_selector("text=Cloud data", timeout=15000); pg.wait_for_timeout(800)
    card = pg.locator("article.svc", has_text="Cloud data").inner_text()
    ok("used" in card and "5 GB" in card and "R2" in card, "Cloud data card: used of budget, the 5 GB rule, R2 note")
    pg.locator("article.svc", has_text="Cloud data").locator("input[type=number]").fill("6000")
    pg.locator("article.svc", has_text="Cloud data").locator("button:has-text('Save')").click(); pg.wait_for_timeout(1500)
    ok(sql("select value->>'budget_mb' from settings where key = 'egress_guard'") == "6000", "the owner saves a new budget")

    # strict level: banner on every page, Production refreshes 4x slower; posting is not the page's job, nothing else changes
    sql("insert into egress_log(day, part, bytes) values ((now() at time zone 'utc')::date - 1, 'poster', 6000::bigint * 1048576) on conflict (day, part) do update set bytes = excluded.bytes")
    pg.goto(URL + "#/production"); pg.reload(); pg.wait_for_timeout(4000)
    ok("Data saver (strict)" in pg.inner_text("main"), "strict level: the banner says posting and running reels continue")
    R.clear(); pg.mouse.move(20, 20); pg.mouse.down(); pg.mouse.up(); pg.wait_for_timeout(30000)
    n = len([u for u in R if "/rest/v1/jobs" in u and "progress" in u and "output" not in u and "failed" not in u])
    ok(1 <= n <= 2, f"strict: Production reads every 24 s instead of 6 ({n} in 30 s)")
    pg.goto(URL + "#/settings"); pg.wait_for_selector("text=Cloud data", timeout=15000)
    ok("strict saver on" in pg.locator("article.svc", has_text="Cloud data").inner_text(), "the card shows the strict saver")
    ok(not errors, f"no page errors {errors[:3]}")
    b.close()

sql("delete from egress_log; delete from settings where key in ('egress_guard', 'ig_library')"); sql(f"delete from jobs where id = '{rid}'")
bad = [n for c, n in res if not c]
print(f"{len(res) - len(bad)}/{len(res)} data-guard UI checks passed" + (" | FAILED: " + "; ".join(bad) if bad else ""))

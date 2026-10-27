"""Re-check of each connection in an account's Settings, against the local stack (mock :8765 -> PostgREST, real SQL).   python recheck_ui.py
The cloud poster is simulated by SQL (it answers the Re-check flag within a minute in production)."""
import subprocess, threading, time
from playwright.sync_api import sync_playwright
URL = "http://127.0.0.1:8765/index.html"; R = []; errors = []
def sql(q): return subprocess.run(["psql", "-h", "/tmp", "-p", "5433", "-U", "postgres", "-d", "rs_it", "-tAc", q], capture_output=True, text=True).stdout.strip()
def ok(c, n): R.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n, flush=True)
sql("update brands set status='live', website='https://bookmycharter.in' where id='bookmycharter'")
sql("delete from settings where key in ('facebook_status@bookmycharter','website_status@bookmycharter','instagram_status@bookmycharter')")
sql("""insert into settings(key,value) values ('facebook_status@bookmycharter', '{"state":"expired","page_id":"1","page_name":"Book My Charter","note":"Facebook connection stopped working (password change or removed access). Connect Facebook again in this account''s Settings.","checked_at":"2026-10-27T06:44:01Z"}'),
  ('website_status@bookmycharter', '{"state":"connected","ms":420,"url":"https://bookmycharter.in","checked_at":"2026-10-27T06:00:00Z"}')""")
def cloud():            # the cloud poster: sees the flag, checks Facebook (the token works), writes the fresh status
    for _ in range(60):
        time.sleep(1)
        if sql("select value ? 'recheck_at' from settings where key='facebook_status@bookmycharter'") == "t":
            time.sleep(3)
            sql("""update settings set value = '{"state":"connected","page_id":"1","page_name":"Book My Charter","checked_at":"2026-10-27T10:00:00Z"}' where key='facebook_status@bookmycharter'""")
            return
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    cx = b.new_context(viewport={"width": 1280, "height": 900}, service_workers="block"); cx.add_init_script("sessionStorage.setItem('rs_checked', String(Date.now()))")
    pg = cx.new_page(); pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(URL); pg.fill("input[type=email]", "weoneaviation8@gmail.com"); pg.fill("input[type=password]", "secret"); pg.click("button[type=submit]"); pg.wait_for_selector(".acct h3", timeout=60000)
    pg.wait_for_timeout(1500)
    fb_dot = pg.get_attribute("article:has-text('Book My Charter') .mini.fb", "title") or ""
    ok("expired" in fb_dot and "Connect Facebook again" in fb_dot, f"Dashboard: the yellow dot explains itself on hover ({fb_dot[:60]}…)")
    ok(pg.query_selector("article:has-text('Book My Charter') .mini.web.ok") is not None, "Dashboard: the website has its own status dot (online)")
    pg.click("[aria-label='Settings of Book My Charter']"); pg.wait_for_selector("text=Connections", timeout=15000)
    card = "article.conn:has(h3:text-is('Facebook'))"
    ok("Connection expired" in pg.inner_text(card) and pg.query_selector(card + " button:has-text('Re-check')"), "Facebook card: expired, with a Re-check button")
    ok(pg.query_selector("article.conn:has(h3:text-is('Website')) button:has-text('Re-check')") is not None and "Online" in pg.inner_text("article.conn:has(h3:text-is('Website'))"), "Website card: online, with a Re-check button")
    ok(pg.query_selector("article.conn:has(h3:text-is('LinkedIn')) button:has-text('Re-check')") is None, "nothing to re-check on a platform that was never connected")
    th = threading.Thread(target=cloud); th.start()
    pg.click(card + " button:has-text('Re-check')"); pg.wait_for_timeout(800)
    ok("Checking" in pg.inner_text(card), "while the cloud checks, the button says Checking…")
    pg.wait_for_selector(card + " .plat-state.ok", timeout=40000); th.join()
    ok("Connected: Book My Charter" in pg.inner_text(card) and "Connect Facebook again" not in pg.inner_text(card), "after the check: Connected, the old warning gone")
    pg.click("nav >> text=Dashboard") if pg.query_selector("nav >> text=Dashboard") else pg.goto(URL + "#/home")
    pg.wait_for_selector(".acct h3", timeout=15000); pg.wait_for_timeout(1500)
    ok(pg.query_selector("article:has-text('Book My Charter') .mini.fb.ok") is not None, "Dashboard: Book My Charter's Facebook dot is green")
    ok(not errors, f"no page errors {errors[:2]}")
bad = [n for k, n in R if not k]; print(f"{len(R) - len(bad)}/{len(R)} re-check UI checks passed" + (" | FAILED: " + "; ".join(bad) if bad else ""))

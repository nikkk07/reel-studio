"""Studio Settings + Account Settings against the real database (local Postgres + PostgREST through mock_supabase proxy):
navigation per level, keys write-only, per-account connections, profile edits, phone layout, Mac offline."""
import subprocess, sys
from playwright.sync_api import sync_playwright
def sql(q): return subprocess.run(["psql","-h","/tmp","-p","5433","-U","postgres","-d","rs_it","-tAc",q],capture_output=True,text=True).stdout.strip()
sql("update brands set status='live'; delete from secrets where key like 'api_%' or key like '%@%' or key='meta_app_secret'; delete from settings where key like '%@%';"
    "update settings set value='{}' where key in ('studio_keys','worker_settings'); delete from settings where key='meta_app';"
    "insert into members(email,role) values ('weoneaviation8@gmail.com','owner') on conflict (email) do update set role='owner';"
    "update workers set last_seen = now() - interval '3 hours'")     # the Mac is offline for this whole test
errs=[]; R=[]
def ok(c,n): R.append((bool(c), n)); print(("PASS " if c else "FAIL ")+n, flush=True)
KEY = "a" * 52 + "WXYZ"
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    for vw, nm in (({"width":1280,"height":900},"d"), ({"width":390,"height":860},"m")):
        cx = b.new_context(viewport=vw, service_workers="block")
        pg = cx.new_page(); pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto("http://127.0.0.1:8765/index.html"); pg.fill("input[type=email]", "weoneaviation8@gmail.com"); pg.fill("input[type=password]", "secret"); pg.click("button[type=submit]")
        pg.wait_for_timeout(2500)
        # the Mac is offline: settings must still open without the pre-flight page
        pg.goto("http://127.0.0.1:8765/index.html#/settings"); pg.wait_for_selector(".svc", timeout=30000); pg.wait_for_timeout(1500)
        ok("Studio settings" in pg.inner_text("main"), f"{nm}: Studio settings open while the Mac is offline")
        navsel = "header nav button" if nm == "d" else ".tabbar button"
        labels = [t.strip() for t in pg.locator(navsel).all_inner_texts()]
        ok(labels == ["Dashboard", "Settings"], f"{nm}: studio level shows only Dashboard + Settings {labels}")
        t = pg.inner_text("main")
        ok(all(x in t for x in ["Stock footage", "Developer apps", "Posting", "Studio Mac", "Claude", "Supabase", "Vercel", "GitHub", "Meta app", "LinkedIn app"]), f"{nm}: all sections and services listed")
        ok(pg.evaluate("[...document.querySelectorAll('.svc-logo img, .svc-ico img')].every(i => i.complete && i.naturalWidth > 10)"), f"{nm}: every service logo loads")
        ok("Offline" in t, f"{nm}: Mac shown offline")
        if nm == "d":
            card = pg.locator(".svc", has_text="Main source of video clips")
            card.locator("input").fill("not a key!"); card.locator("button:has-text('Save key')").click(); pg.wait_for_timeout(1200)
            ok("does not look like a Pexels key" in card.inner_text(), "bad Pexels key refused with a clear message")
            card.locator("input").fill(KEY); card.locator("button:has-text('Save key')").click(); pg.wait_for_timeout(2500)
            card = pg.locator(".svc", has_text="Main source of video clips")
            ok("…WXYZ" in card.inner_text() and "Checking" in card.inner_text(), "saved: only the last 4 characters shown, cloud check pending")
            ok(KEY not in pg.content(), "the full key is nowhere on the page")
            ok(sql("select value from secrets where key='api_pexels'") == KEY, "key stored server side")
            m = pg.locator(".svc", has_text="Meta app"); m.locator("input").nth(0).fill("123456789"); m.locator("input").nth(1).fill("ab" * 16)
            m.locator("button:has-text('Save')").click(); pg.wait_for_timeout(2500)
            ok("…abab" in pg.locator(".svc", has_text="Meta app").inner_text() or sql("select value->>'app_id' from settings where key='meta_app'") == "123456789", "Meta app saved once for every account")
            pg.screenshot(path="/tmp/rs_stack/settings_d.png", full_page=True)
        else:
            pg.screenshot(path="/tmp/rs_stack/settings_m.png", full_page=True)
        ok(pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), f"{nm}: no sideways scroll (studio settings)")
        # account settings: from the Dashboard card
        pg.goto("http://127.0.0.1:8765/index.html#/home"); pg.wait_for_selector(".acct h3", timeout=30000); pg.wait_for_timeout(1200)
        pg.locator(".acct", has_text="Flying Star Aviator").locator("button:has-text('Settings')").click(); pg.wait_for_timeout(2500)
        ok("#/account" in pg.url and "Flying Star Aviator" in pg.inner_text("header"), f"{nm}: Dashboard card → that account's Settings")
        labels = [t.strip() for t in pg.locator(navsel).all_inner_texts()]
        ok(labels == ["Create", "Production", "Library", "Insights", "Agents", "Settings"], f"{nm}: account level shows Create/Production/Library/Insights/Agents/Settings {labels}")
        t = pg.inner_text("main")
        ok(all(x in t for x in ["Profile", "Assets", "Connections", "Instagram", "Facebook", "LinkedIn", "Website"]), f"{nm}: profile, logo and 4 connections")
        if nm == "d":
            pg.locator("input[name=tagline]").fill("CPL & ATPL ground school, Dwarka"); pg.locator("input[name=linkedin]").fill("https://www.linkedin.com/company/flying-star-aviator")
            pg.locator("button:has-text('Save profile')").click(); pg.wait_for_timeout(2000)
            ok(sql("select tagline || '|' || (links->>'linkedin') from brands where id='flystar'") == "CPL & ATPL ground school, Dwarka|https://www.linkedin.com/company/flying-star-aviator", "profile saved to the account")
            ig = pg.locator(".svc.conn", has_text="Instagram"); ig.locator("button:has-text('Connect Instagram')").click(); pg.wait_for_timeout(600)
            ok("Flying Star Aviator's Instagram" in pg.inner_text("dialog"), "Connect Instagram names the account")
            pg.locator("dialog input").fill("IGAAflystar" + "x" * 60); pg.locator("dialog button:has-text('Save and check')").click(); pg.wait_for_timeout(2500)
            ok(sql("select value from secrets where key='instagram_token@flystar'").startswith("IGAAflystar"), "token stored under Flying Star, not We One")
            ok(sql("select count(*) from secrets where key='instagram_token' and value like 'IGAAflystar%'") == "0", "We One's token untouched")
            pg.keyboard.press("Escape"); pg.wait_for_timeout(500)
            sql("""update settings set value='{"state":"connected","username":"flyingstaraviator","user_id":"7"}' where key='instagram_status@flystar'""")
            pg.wait_for_timeout(9000)
            ok("@flyingstaraviator" in pg.locator(".svc.conn", has_text="Instagram").inner_text(), "Flying Star shows its own connected Instagram")
            fb = pg.locator(".svc.conn", has_text="Facebook"); fb.locator("button:has-text('Connect Facebook')").click(); pg.wait_for_timeout(600)
            d = pg.inner_text("dialog"); ok("Uses the Meta app saved in Studio Settings" in d and pg.locator("dialog input").count() == 1, "Facebook connect only asks for the token (Meta app saved once)")
            pg.keyboard.press("Escape"); pg.wait_for_timeout(400)
            pg.screenshot(path="/tmp/rs_stack/account_d.png", full_page=True)
            pg.goto("http://127.0.0.1:8765/index.html#/library"); pg.wait_for_timeout(2500)
            ok(pg.locator(".plat").count() == 4, "Flying Star library shows its own connection tiles")
            pg.locator(".plat.ig").click(); pg.wait_for_timeout(1200); ok("#/account" in pg.url, "tapping a tile opens the account's Settings")
            pg.goto("http://127.0.0.1:8765/index.html#/home"); pg.wait_for_timeout(2000)
            ok("ok" in pg.locator(".acct", has_text="Flying Star Aviator").locator(".mini.ig").get_attribute("class"), "Dashboard dot: Flying Star Instagram connected")
            ok("no" in pg.locator(".acct", has_text="Book My Charter").locator(".mini.ig").get_attribute("class"), "Dashboard dot: Book My Charter still not connected")
        else:
            pg.screenshot(path="/tmp/rs_stack/account_m.png", full_page=True)
        ok(pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), f"{nm}: no sideways scroll (account settings)")
    b.close()
ok(not errs, f"no page errors {errs[:2]}")
bad = [n for k, n in R if not k]; print(f"{len(R) - len(bad)}/{len(R)} settings UI checks passed" + (" | FAILED: " + "; ".join(bad) if bad else "")); sys.exit(1 if bad else 0)

"""YouTube Shorts: the Google app in Studio Settings, Connect YouTube per account, the sign-in round trip, posting controls.
Local stack (mock :8765 -> PostgREST, real SQL). Google and the yt-auth function are simulated.   python youtube_ui.py"""
import json, subprocess
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright
URL = "http://127.0.0.1:8765/index.html"; R = []; errors = []
def sql(q): return subprocess.run(["psql", "-h", "/tmp", "-p", "5433", "-U", "postgres", "-d", "rs_it", "-tAc", q], capture_output=True, text=True).stdout.strip()
def ok(c, n): R.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n, flush=True)
before = sql("select string_agg(id||'='||status, ',') from brands")
sql("update brands set status='live' where id='flystar'")
sql("delete from settings where key like 'youtube%'; delete from secrets where key like 'youtube%'; delete from jobs where kind='reel'")
CID = "123456789012-" + "abcdefghijklmnopqrstuvwxyz123456" + ".apps." + "googleusercontent.com"; FAKE_SECRET = "GOCSPX" + "-" + "abcdefghijklmnopqrstuvwxyz12"   # test values, built at run time
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    cx = b.new_context(viewport={"width": 1280, "height": 900}, service_workers="block"); cx.add_init_script("sessionStorage.setItem('rs_checked', String(Date.now()))")
    pg = cx.new_page(); pg.on("pageerror", lambda e: errors.append(str(e)))
    google = []
    pg.route("https://accounts.google.com/**", lambda r: (google.append(r.request.url), r.fulfill(status=200, body="google")))
    def yt_auth(route):
        body = json.loads(route.request.post_data or "{}")
        if body.get("code") == "good" and body.get("brand") == "flystar":
            sql("""insert into settings(key,value) values ('youtube_status@flystar','{"state":"connected","channel_id":"UCfs","channel_title":"Flying Star Aviator","handle":"@flyingstar"}')
                   on conflict (key) do update set value = excluded.value""")
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "channel_title": "Flying Star Aviator", "handle": "@flyingstar"}))
        else: route.fulfill(status=400, content_type="application/json", body=json.dumps({"error": "Google did not accept the sign-in (check the Client ID and Client Secret in Studio Settings)."}))
    pg.route("**/functions/v1/yt-auth", yt_auth)
    pg.goto(URL); pg.fill("input[type=email]", "weoneaviation8@gmail.com"); pg.fill("input[type=password]", "secret"); pg.click("button[type=submit]")
    pg.wait_for_selector(".acct h3", timeout=60000); pg.wait_for_timeout(800)
    ok(pg.query_selector("article:has-text('Flying Star Aviator') .mini.yt") is not None, "Dashboard: each account has a YouTube dot")
    # 1. Studio Settings: the Google app
    pg.goto(URL + "#/settings"); pg.wait_for_selector("article:has(h3:text-is('YouTube app (Google)'))", timeout=20000)
    card = "article:has(h3:text-is('YouTube app (Google)'))"; t = pg.inner_text(card)
    ok("Not saved" in t and "https://reel-studio-hq.vercel.app/" in t and "https://weone-reelstudio.vercel.app/" in t and "Publish app" in t, "app card: redirect URIs and the 7-day Testing warning")
    pg.fill(card + " input[aria-label='YouTube Client ID']", "abc"); pg.fill(card + " input[aria-label='YouTube Client Secret']", FAKE_SECRET)
    pg.click(card + " button:has-text('Save')"); pg.wait_for_timeout(1200)
    ok("Client ID looks wrong" in pg.inner_text(card), "a wrong Client ID is explained")
    pg.fill(card + " input[aria-label='YouTube Client ID']", CID); pg.fill(card + " input[aria-label='YouTube Client Secret']", FAKE_SECRET)
    pg.click(card + " button:has-text('Save')"); pg.wait_for_timeout(1500)
    ok("Saved · Client ID" in pg.inner_text(card) and pg.input_value(card + " input[aria-label='YouTube Client Secret']") == "", "app saved; the secret field is cleared, never shown again")
    ok(sql("select value from secrets where key='youtube_client_secret'").startswith("GOCSPX-"), "secret stored server side")
    # 2. Flying Star's Settings: Connect YouTube -> Google
    pg.evaluate("localStorage.setItem('rs_brand','flystar')"); pg.goto(URL + "#/account"); pg.reload()
    yc = "article.conn:has(h3:text-is('YouTube'))"; pg.wait_for_selector(yc, timeout=20000)
    ok("Not connected" in pg.inner_text(yc), "Flying Star: YouTube not connected")
    pg.click(yc + " button:has-text('Connect YouTube')"); pg.wait_for_selector("dialog[aria-label='Connect YouTube']")
    ok("unverified" in pg.inner_text("dialog") and "Manage your YouTube videos" in pg.inner_text("dialog"), "dialog explains Google's screens")
    pg.click("dialog button:has-text('Sign in with Google')"); pg.wait_for_timeout(1500)
    q = parse_qs(urlparse(google[-1]).query) if google else {}
    ok(q.get("client_id") == [CID] and q.get("access_type") == ["offline"] and "youtube.upload" in q.get("scope", [""])[0] and q.get("state", [""])[0].startswith("yt"), "Google sign-in asks for upload + offline access")
    state = q.get("state", [""])[0]
    # 3. back from Google: a refused code, then a good one
    pg.goto(URL + "#/account"); pg.wait_for_timeout(500)
    pg.evaluate(f"sessionStorage.setItem('yt_state','{state}'); sessionStorage.setItem('yt_brand','flystar')")
    pg.goto(f"http://127.0.0.1:8765/index.html?code=bad&state={state}"); pg.wait_for_timeout(2500)
    ok("check the Client ID" in pg.inner_text("body"), "a refused sign-in shows Google's reason")
    pg.evaluate(f"sessionStorage.setItem('yt_state','{state}'); sessionStorage.setItem('yt_brand','flystar')")
    pg.goto(f"http://127.0.0.1:8765/index.html?code=good&state={state}"); pg.wait_for_selector(yc + " .plat-state.ok", timeout=20000)
    ok("Connected: Flying Star Aviator" in pg.inner_text(yc) and "@flyingstar" in pg.inner_text(yc), "after Google: Flying Star's channel connected")
    ok("code=" not in pg.url, "the one-time code is removed from the address bar")
    pg.evaluate("sessionStorage.setItem('yt_state','ytforged'); sessionStorage.setItem('yt_brand','flystar')")
    pg.goto("http://127.0.0.1:8765/index.html?code=good&state=ytother"); pg.wait_for_timeout(2000)
    ok("could not be verified" in pg.inner_text("body"), "a forged sign-in return is refused")
    # 4. Library: Post to YouTube on an approved reel
    sql("""insert into jobs(kind,brand,status,input,output) values ('reel','flystar','done','{}','{"title":"FS reel","ig_video":"x/ig.mp4","duration":30}')""")
    pg.goto(URL + "#/library"); pg.wait_for_selector(".libcard", timeout=20000); pg.wait_for_timeout(800)
    ok("Not posted to YouTube" in pg.inner_text(".libcard"), "Library: YouTube status on the reel")
    pg.click(".libcard button:has-text('Post to YouTube')"); pg.wait_for_timeout(1500)
    ok(sql("select output->'yt'->>'state' from jobs where output->>'title'='FS reel'") == "scheduled", "Post to YouTube schedules the upload")
    pg.screenshot(path="/tmp/rs_stack/yt_library.png", full_page=True)
    # 5. Dashboard dot green; mobile has no sideways scroll
    pg.goto(URL + "#/home"); pg.wait_for_selector(".acct h3", timeout=20000); pg.wait_for_timeout(1000)
    ok(pg.query_selector("article:has-text('Flying Star Aviator') .mini.yt.ok") is not None, "Dashboard: Flying Star's YouTube dot is green")
    pg.set_viewport_size({"width": 390, "height": 844}); pg.wait_for_timeout(600)
    sw = pg.evaluate("document.documentElement.scrollWidth"); ok(sw <= 391, f"mobile dashboard: no sideways scroll ({sw}px)")
    pg.screenshot(path="/tmp/rs_stack/yt_dash_m.png", full_page=False)
    pg.goto(URL + "#/account"); pg.wait_for_selector(yc, timeout=20000); pg.wait_for_timeout(500)
    sw = pg.evaluate("document.documentElement.scrollWidth"); ok(sw <= 391, f"mobile account settings: no sideways scroll ({sw}px)")
    pg.locator(yc).screenshot(path="/tmp/rs_stack/yt_card_m.png")
    ok(not errors, f"no page errors {errors[:2]}")
for kv in before.split(","): k, v = kv.split("="); sql(f"update brands set status='{v}' where id='{k}'")
bad = [n for k, n in R if not k]; print(f"{len(R) - len(bad)}/{len(R)} YouTube UI checks passed" + (" | FAILED: " + "; ".join(bad) if bad else ""))

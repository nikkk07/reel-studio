"""Studio Settings: Groq, Coverr and ThumbAPI keys (owner only, validated, never shown again) and the AI-thumbnail switch.
Local stack (mock :8765 -> PostgREST, real SQL).   python apis_ui.py"""
import subprocess
from playwright.sync_api import sync_playwright
URL = "http://127.0.0.1:8765/index.html"; R = []; errors = []
def sql(q): return subprocess.run(["psql", "-h", "/tmp", "-p", "5433", "-U", "postgres", "-d", "rs_it", "-tAc", q], capture_output=True, text=True).stdout.strip()
def ok(c, n): R.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n, flush=True)
GROQ = "gsk_" + "a1b2c3d4e5" * 5          # test value built at run time
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    cx = b.new_context(viewport={"width": 1280, "height": 900}, service_workers="block"); cx.add_init_script("sessionStorage.setItem('rs_checked', String(Date.now()))")
    pg = cx.new_page(); pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(URL); pg.fill("input[type=email]", "weoneaviation8@gmail.com"); pg.fill("input[type=password]", "secret"); pg.click("button[type=submit]")
    pg.wait_for_selector(".acct h3", timeout=60000); pg.goto(URL + "#/settings")
    g = "article.svc:has(h3:text-is('Groq'))"; c = "article.svc:has(h3:text-is('Coverr'))"; t = "article.svc:has(h3:text-is('ThumbAPI'))"
    pg.wait_for_selector(g, timeout=20000)
    ok("Backup brain" in pg.inner_text(g) and "Whisper large-v3" in pg.inner_text(g), "Groq card explains what it does")
    ok(pg.query_selector(c + " a[href='https://coverr.co']") is not None, "Coverr card carries the clickable Coverr credit")
    ok("5 thumbnails" in pg.inner_text(t) and "watermark" in pg.inner_text(t), "ThumbAPI card is honest about the free plan")
    ok(pg.get_attribute(t + " button[role=switch]", "disabled") is not None, "thumbnail switch stays off until a key is saved")
    pg.fill(g + " input[type=password]", "sk-not-groq"); pg.click(g + " button:has-text('Save key')"); pg.wait_for_timeout(1200)
    ok("starts with gsk_" in pg.inner_text(g), "a wrong Groq key is explained")
    pg.fill(g + " input[type=password]", GROQ); pg.click(g + " button:has-text('Save key')"); pg.wait_for_timeout(1500)
    ok(sql("select value from secrets where key='api_groq'") == GROQ and "Checking" in pg.inner_text(g), "Groq key saved server side, now being checked")
    ok(GROQ not in pg.content(), "the key is never shown on the page")
    pg.fill(t + " input[type=password]", "thumb_" + "k" * 20); pg.click(t + " button:has-text('Save key')"); pg.wait_for_timeout(1500)
    pg.click(t + " button[role=switch]"); pg.wait_for_timeout(1500)
    ok(sql("select value->>'on' from settings where key='thumbapi_use'") == "true" and pg.get_attribute(t + " button[role=switch]", "aria-checked") == "true", "AI thumbnails switched on")
    pg.click(t + " button[role=switch]"); pg.wait_for_timeout(1200)
    ok(sql("select value->>'on' from settings where key='thumbapi_use'") == "false", "and off again")
    pg.set_viewport_size({"width": 390, "height": 844}); pg.wait_for_timeout(500)
    w = pg.evaluate("document.documentElement.scrollWidth"); ok(w <= 391, f"mobile: no sideways scroll ({w}px)")
    pg.locator(t).screenshot(path="/tmp/rs_stack/thumb_card.png")
    ok(not errors, f"no page errors {errors[:2]}")
bad = [n for k, n in R if not k]; print(f"{len(R) - len(bad)}/{len(R)} new-API UI checks passed" + (" | FAILED: " + "; ".join(bad) if bad else ""))

"""Visual walkthrough against mock_supabase.py; saves screenshots for review.  python ui_visual.py <out_dir> <port>"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True); URL = f"http://127.0.0.1:{sys.argv[2]}/index.html"
errs = []
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    for name, vp in [("desk", {"width": 1366, "height": 860}), ("mob", {"width": 390, "height": 844})]:
        ctx = b.new_context(viewport=vp, device_scale_factor=1); pg = ctx.new_page()
        pg.on("pageerror", lambda e: errs.append(f"{name}: {e}")); pg.on("console", lambda m: m.type == "error" and errs.append(f"{name}: {m.text}"))
        pg.goto(URL); pg.wait_for_selector("text=Sign in"); pg.wait_for_timeout(2600); pg.screenshot(path=OUT / f"{name}_1_login.png")
        pg.fill("#em", "weoneaviation8@gmail.com"); pg.fill("#pw", "secret"); pg.click("button[type=submit]")
        pg.wait_for_selector("text=Pre-flight check"); pg.click("text=Connect to Server"); pg.wait_for_timeout(2600); pg.screenshot(path=OUT / f"{name}_2_preflight.png")
        pg.wait_for_selector("text=Create a reel", timeout=20000)
        pg.click("text=Generate suggestions"); pg.wait_for_timeout(1800); pg.screenshot(path=OUT / f"{name}_3_thinking.png")
        pg.wait_for_selector(".topic", timeout=20000); pg.locator(".topic").nth(1).click(); pg.wait_for_timeout(500)
        pg.screenshot(path=OUT / f"{name}_4_create.png", full_page=(name == "desk"))
        pg.click("text=Create reel"); pg.wait_for_selector(".strip", timeout=10000); pg.wait_for_timeout(9000)
        pg.screenshot(path=OUT / f"{name}_5_production.png", full_page=True)
        pg.locator(".wp-btn").nth(0).click(); pg.wait_for_timeout(800); pg.screenshot(path=OUT / f"{name}_6_stage.png", full_page=True)
        pg.wait_for_selector("text=Ready for your review", timeout=120000); pg.wait_for_timeout(2500)
        pg.screenshot(path=OUT / f"{name}_7_review.png", full_page=True)
        pg.click("button:has-text('Approve')"); pg.wait_for_timeout(1200)
        pg.goto(URL + "#/library"); pg.wait_for_selector(".poster", timeout=10000); pg.wait_for_timeout(800); pg.screenshot(path=OUT / f"{name}_8_library.png")
        pg.goto(URL + "#/create"); pg.click("text=Use my idea"); pg.fill("#idea", "pilot aur co-pilot alag khana kyun khaate hain"); pg.click("text=Shape my idea")
        pg.wait_for_selector("text=Make a new version", timeout=20000); pg.screenshot(path=OUT / f"{name}_9_idea.png", full_page=(name == "desk"))
        ctx.close()
    b.close()
print("errors:", errs or "none")

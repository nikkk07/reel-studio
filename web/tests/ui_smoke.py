"""UI smoke test against mock_supabase.py:  python ui_smoke.py <out_dir> [port]"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
URL = f"http://127.0.0.1:{sys.argv[2] if len(sys.argv) > 2 else 8765}/index.html"
errors = []
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    pg = b.new_page(viewport={"width": 420, "height": 900})
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: m.type == "error" and errors.append(m.text))
    pg.goto(URL)
    pg.fill("input[type=email]", "owner@example.com"); pg.fill("input[type=password]", "wrong"); pg.click("text=Sign in >> nth=-1")
    expect(pg.locator("text=Invalid login credentials")).to_be_visible(timeout=5000)
    pg.fill("input[type=password]", "secret"); pg.click("button[type=submit]")
    expect(pg.locator("text=Connect to Server").first).to_be_visible(timeout=5000); pg.screenshot(path=OUT / "1_connect.png")
    pg.click("button:has-text('Connect to Server')")
    pg.wait_for_selector("text=Signed in and ready", timeout=15000, state="attached"); pg.screenshot(path=OUT / "2_green.png")
    expect(pg.locator(".tabs")).to_be_visible(timeout=5000)
    pg.click("text=Generate Suggestions")
    expect(pg.locator(".card h3")).to_have_count(5, timeout=60000); pg.screenshot(path=OUT / "3_suggestions.png", full_page=True)
    pg.locator(".card h3").first.click(); pg.click("text=Confirm & Create Reel")
    expect(pg.locator("text=ready for review")).to_be_visible(timeout=int(__import__("os").environ.get("REVIEW_TIMEOUT_MS", "30000")))
    pg.wait_for_timeout(1500); pg.screenshot(path=OUT / "4_review.png", full_page=True)
    assert pg.locator("video").count() == 1
    pg.click("button:has-text('Approve')"); pg.wait_for_timeout(1000)
    pg.click(".tabs >> text=Library"); expect(pg.locator(".lib .card")).to_have_count(1, timeout=5000); pg.screenshot(path=OUT / "5_library.png")
    pg.click(".tabs >> text=My topic"); pg.fill("textarea", "plane par bijli girne se kya hota hai?"); pg.click("text=Optimize Topic")
    expect(pg.locator("text=Re-generate")).to_be_visible(timeout=15000); pg.screenshot(path=OUT / "6_mytopic.png", full_page=True)
    pg.set_viewport_size({"width": 1280, "height": 900}); pg.screenshot(path=OUT / "7_desktop.png")
    b.close()
print("console/page errors:", errors or "none")
print("UI SMOKE OK")

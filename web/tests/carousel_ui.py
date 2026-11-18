"""A CRM-planned carousel in review shows its slides (not a video player).   python carousel_ui.py <out_dir> [port]"""
import importlib.util, sys, threading, time, uuid
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8777
WEB = Path(__file__).resolve().parents[1]
sys.argv = ["mock", str(WEB), str(WEB.parent / "worker/tests/fixtures"), "/nonexistent.mp4", str(PORT)]
spec = importlib.util.spec_from_file_location("mock", Path(__file__).with_name("mock_supabase.py")); M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)

jid = str(uuid.uuid4())
for i in range(1, 4):                       # three tiny JPEG slides in mock storage
    from PIL import Image
    p = M.STORE / jid / f"slide_{i:02d}.jpg"; p.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1080, 1350), (20 * i, 40, 90)).save(p)
M.DB["jobs"][jid] = {"id": jid, "brand": "weone", "kind": "reel", "status": "review", "stage": "publish", "progress": 1, "attempts": 1, "parent_id": None,
                     "input": {"crm_item": 12, "format": "carousel", "concept": {"title": "Pilot after 12th: 6 steps"}}, "error": None, "not_before": None,
                     "output": {"title": "Pilot after 12th: 6 steps", "format": "carousel", "slides": [f"{jid}/slide_0{i}.jpg" for i in range(1, 4)],
                                "ig_caption": "Pilot kaise bane", "review_deadline": M.NOW()},
                     "created_at": M.NOW(), "updated_at": M.NOW(), "lease_owner": None, "lease_expires_at": None}
(WEB / "config.js").write_text(f'export const SUPABASE_URL = "http://127.0.0.1:{PORT}";\nexport const SUPABASE_ANON_KEY = "anon";\n')
threading.Thread(target=M.ThreadingHTTPServer(("127.0.0.1", PORT), M.H).serve_forever, daemon=True).start(); time.sleep(0.5)

errors, R = [], []
def ok(c, n): R.append(bool(c)); print(("PASS " if c else "FAIL ") + n, flush=True)
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path=next(str(p) for p in Path("/opt/pw-browsers").glob("chromium-*/chrome-linux*/chrome")))
    pg = b.new_page(viewport={"width": 420, "height": 900})
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"http://127.0.0.1:{PORT}/index.html")
    pg.fill("input[type=email]", "owner@example.com"); pg.fill("input[type=password]", "secret"); pg.click("button[type=submit]")
    pg.wait_for_timeout(2500); pg.screenshot(path=OUT / "after_signin.png")
    if pg.locator("button:has-text('Connect to Server')").count():
        pg.click("button:has-text('Connect to Server')"); pg.wait_for_selector("text=Signed in and ready", timeout=15000, state="attached")
    pg.evaluate("location.hash = '#/production'")
    expect(pg.locator(".slides img")).to_have_count(3, timeout=15000)
    pg.wait_for_function("[...document.querySelectorAll('.slides img')].every(i => i.complete && i.naturalWidth === 1080)", timeout=10000)
    ok(pg.locator(".slides").get_attribute("aria-label") == "Carousel, 3 slides" and pg.locator("video").count() == 0,
       "CU01 a carousel in review shows its 3 slides, no video player")
    ok(pg.locator("button:has-text('Approve')").count() >= 1, "CU02 it is approved or held like a reel")
    pg.screenshot(path=OUT / "carousel_review.png", full_page=True)
    ok(not errors, "CU03 no page errors " + "; ".join(errors)[:200])
    b.close()
sys.exit(0 if all(R) else 1)

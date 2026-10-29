"""A reel's own Comment-to-DM is visible on its review screen.   python dm_reel_ui.py (local stack)"""
import json, subprocess
from playwright.sync_api import sync_playwright
URL = "http://127.0.0.1:8765/index.html"; R = []; errors = []
def sql(q): return subprocess.run(["psql", "-h", "/tmp", "-p", "5433", "-U", "postgres", "-d", "rs_it", "-tAc", q], capture_output=True, text=True).stdout.strip()
def ok(c, n): R.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n, flush=True)
dm = {"keyword": "MEDICAL", "offer": "the 4-step DGCA medical checklist", "message": "Hi {name}, thank you for your comment.\n\nHere is the 4-step DGCA medical checklist:\n1. Get the Class 2 medical first.",
      "reply": "Sent! Check your messages for the checklist."}
sql(f"""insert into jobs(kind,brand,status,input,output) values ('reel','weone','review','{{}}', $j${json.dumps({"title": "DGCA medical", "duration": 30, "dm": dm})}$j$)""")
jid = sql("select id from jobs where output->>'title'='DGCA medical' order by created_at desc limit 1")
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    cx = b.new_context(viewport={"width": 1280, "height": 900}, service_workers="block"); cx.add_init_script("sessionStorage.setItem('rs_checked', String(Date.now()))")
    pg = cx.new_page(); pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(URL); pg.fill("input[type=email]", "weoneaviation8@gmail.com"); pg.fill("input[type=password]", "secret"); pg.click("button[type=submit]")
    pg.wait_for_selector(".acct h3", timeout=60000); pg.goto(URL + f"#/production/{jid}"); pg.wait_for_selector("summary:has-text('Comment-to-DM')", timeout=20000)
    ok("viewers comment MEDICAL to get the 4-step DGCA medical checklist" in pg.inner_text("summary:has-text('Comment-to-DM')"), "review screen names the reel's keyword and offer")
    pg.click("summary:has-text('Comment-to-DM')"); t = pg.inner_text("details:has(summary:has-text('Comment-to-DM'))")
    ok("Hi @viewer, thank you for your comment." in t and "Sent! Check your messages" in t, "the exact DM and public reply can be read before approving")
    ok(not errors, f"no page errors {errors[:2]}")
sql(f"delete from jobs where id='{jid}'")
bad = [n for k, n in R if not k]; print(f"{len(R) - len(bad)}/{len(R)} reel DM UI checks passed" + (" | FAILED: " + "; ".join(bad) if bad else ""))

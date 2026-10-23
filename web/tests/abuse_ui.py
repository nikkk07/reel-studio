"""Hostile content in the browser: script tags, javascript: links, giant/odd text, RTL tricks. Nothing may execute or break layout."""
import json, subprocess, sys, uuid
from playwright.sync_api import sync_playwright
URL = "http://127.0.0.1:8765/index.html"; R = []; errors = []; dialogs = []
def sql(q): return subprocess.run(["psql", "-h", "/tmp", "-p", "5433", "-U", "postgres", "-d", "rs_it", "-tAc", q], capture_output=True, text=True).stdout.strip()
def ok(c, n): R.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n, flush=True)
X = '<img src=x onerror="window.__pwned=1">'; RTL = "‮evil‬"; LONG = "W" * 400
sql("delete from jobs where kind='suggest'; update jobs set status='cancelled' where kind='reel' and status in ('queued','running','review','waiting_quota')")
topics = [{"title": X + " title", "hook": "<script>window.__pwned=2</script>", "angle": LONG, "why_viral": RTL, "key_facts": [X], "est_seconds": 30,
           "sources": [{"title": "evil", "url": "javascript:window.__pwned=3"}, {"title": "data", "url": "data:text/html,<script>alert(1)</script>"}, {"title": "ok", "url": "https://www.faa.gov/"}]}] * 2
sql(f"insert into jobs(kind,status,input,output) values ('suggest','done','{{}}', '{json.dumps({'topics': topics}).replace(chr(39), chr(39)*2)}')")
ap = {"autopilot": {"title": X, "hook": "<b>bold?</b>", "category": "news", "score": 90, "why": RTL + LONG, "reel_id": str(uuid.uuid4()), "day": "2026-10-23"}, "topics": topics}
sql(f"insert into jobs(kind,status,input,output) values ('suggest','done','{{\"autopilot\":true,\"day\":\"2026-10-23\",\"trigger\":\"manual\"}}', '{json.dumps(ap).replace(chr(39), chr(39)*2)}')")
rid = str(uuid.uuid4())
out = {"title": X, "qc": {"score": 3, "summary": X, "issues": [{"t": 1, "problem": X}]}, "duration": 40, "ig_caption": X + "\n" + LONG, "claims": [{"claim": X, "source_url": "javascript:window.__pwned=4", "group": "G1"}],
       "script": [{"t": 0, "end": 2, "text": "*" + X + "*", "group": "G1"}], "autopilot_note": X}
sql(f"insert into jobs(id,kind,status,input,output) values ('{rid}','reel','running','{{\"concept\":{{\"title\":\"x\"}}}}', '{json.dumps(out).replace(chr(39), chr(39)*2)}')"); sql(f"update jobs set status='review' where id='{rid}'")
sql(f"insert into job_events(job_id,level,message) values ('{rid}','info','{X.replace(chr(39), chr(39)*2)}')")
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    for vw in ({"width": 1280, "height": 900}, {"width": 360, "height": 800}):
        cx = b.new_context(viewport=vw, service_workers="block"); cx.add_init_script("sessionStorage.setItem('rs_checked', String(Date.now()))")
        pg = cx.new_page(); pg.on("pageerror", lambda e: errors.append(str(e))); pg.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
        pg.goto(URL); pg.fill("input[type=email]", "weoneaviation8@gmail.com"); pg.fill("input[type=password]", "secret"); pg.click("button[type=submit]")
        pg.wait_for_selector(".ap h3", timeout=60000); pg.wait_for_timeout(2500)
        for sel in [".topic"]: 
            try: pg.click(sel, timeout=3000)
            except Exception: pass
        links = pg.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
        ok(not any(str(l).lower().startswith(("javascript:", "data:")) for l in links), f"no javascript:/data: links rendered on Create ({vw['width']}px)")
        ok("<img src=x" in pg.inner_text("main"), "hostile text is shown as plain text")
        try: pg.click("nav.tabbar >> text=Production" if vw["width"] < 700 else "nav >> text=Production", timeout=8000)
        except Exception as e: pg.screenshot(path="/tmp/rs_stack/abuse_fail.png"); print("CLICKFAIL", str(e).splitlines()[-3:]); pg.goto(URL + "#/production")
        pg.wait_for_timeout(3000)
        try: pg.click(f"text=Ready", timeout=3000)
        except Exception: pass
        pg.wait_for_timeout(1500)
        links = pg.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
        ok(not any(str(l).lower().startswith(("javascript:", "data:")) for l in links), "no javascript: links in Production (claims, captions)")
        ok(pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), f"400-character words don't break the layout ({vw['width']}px)")
        ok(pg.evaluate("!window.__pwned"), "no injected script ran")
    ok(not dialogs and not errors, f"no alerts/page errors {dialogs[:1]} {errors[:1]}")
    b.close()
sql(f"delete from job_events where job_id='{rid}'; delete from jobs where id='{rid}'; delete from jobs where kind='suggest'")
bad = [n for k, n in R if not k]; print(f"{len(R) - len(bad)}/{len(R)} hostile-content UI checks passed" + (" | FAILED: " + "; ".join(bad) if bad else "")); sys.exit(1 if bad else 0)

"""A reel's own Comment-to-DM is visible on its review screen.   python dm_reel_ui.py (local stack)"""
import json, subprocess
from playwright.sync_api import sync_playwright
URL = "http://127.0.0.1:8765/index.html"; R = []; errors = []
def sql(q): return subprocess.run(["psql", "-h", "/tmp", "-p", "5433", "-U", "postgres", "-d", "rs_it", "-tAc", q], capture_output=True, text=True).stdout.strip()
def ok(c, n): R.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n, flush=True)
dm = {"keyword": "MEDICAL", "offer": "the 4-step DGCA medical checklist", "message": "Hi {name}, thank you for your comment.\n\nHere is the 4-step DGCA medical checklist:\n1. Get the Class 2 medical first.",
      "reply": "Sent! Check your messages for the checklist."}
sql(f"""insert into jobs(kind,brand,status,input,output) values ('reel','weone','review','{{}}', $j${json.dumps({"title": "DGCA medical", "duration": 30, "dm": dm, "retention": {"hooks": [{"rank": 1, "line": "Medical fail, sapna khatam?", "form": "question", "why": "loss aversion"},
    {"rank": 2, "line": "Ek test ne 3 saal kha liye", "form": "micro_story", "why": "status"}, {"rank": 3, "line": "Yeh form mat bharna jab tak", "form": "open_statement", "why": "open loop"}],
    "beats": [{"group": "G1", "role": "hook", "seconds": "0-3", "mechanism": "loss aversion", "why": "parents' money at stake"}, {"group": "G2", "role": "rehook", "seconds": "14-18", "mechanism": "raised consequence", "why": "restarts attention"}]}})}$j$)""")
jid = sql("select id from jobs where output->>'title'='DGCA medical' order by created_at desc limit 1")
sql("delete from dm_log where comment_id like 'kwt%'; delete from jobs where output->>'title' in ('KW test A','KW test B')")
for t, kw, mid, h_, reach in [("KW test A", "MEDICAL", "kwm1", 72, 2000), ("KW test B", "FEES", "kwm2", 10, 900)]:
    sql(f"""insert into jobs(kind,brand,status,input,output) values ('reel','weone','done','{{}}', jsonb_build_object('title','{t}','dm',jsonb_build_object('keyword','{kw}'),
        'ig',jsonb_build_object('state','posted','media_id','{mid}','posted_at',to_char((now() - interval '{h_} hours') at time zone 'utc','YYYY-MM-DD"T"HH24:MI:SS"Z"')),'ig_stats',jsonb_build_object('reach',{reach})))""")
for i in range(9): sql(f"insert into dm_log(brand,comment_id,media_id,username,keyword,state) values ('weone','kwt{i}','kwm1','u{i}','MEDICAL','sent')")
sql("insert into dm_log(brand,comment_id,media_id,username,keyword,state) values ('weone','kwtx','kwm1','ux','PILOT','sent')")
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    cx = b.new_context(viewport={"width": 1280, "height": 900}, service_workers="block"); cx.add_init_script("sessionStorage.setItem('rs_checked', String(Date.now()))")
    pg = cx.new_page(); pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(URL); pg.fill("input[type=email]", "weoneaviation8@gmail.com"); pg.fill("input[type=password]", "secret"); pg.click("button[type=submit]")
    pg.wait_for_selector(".acct h3", timeout=60000); pg.goto(URL + f"#/production/{jid}"); pg.wait_for_selector("summary:has-text('Comment-to-DM')", timeout=20000)
    ok("viewers comment MEDICAL to get the 4-step DGCA medical checklist" in pg.inner_text("summary:has-text('Comment-to-DM')"), "review screen names the reel's keyword and offer")
    pg.click("summary:has-text('Hook options')"); rt = pg.inner_text("details:has(summary:has-text('Hook options'))")
    ok("Medical fail, sapna khatam?" in rt and "(used)" in rt and "rehook" in rt and "raised consequence" in rt, "review screen shows the 3 ranked hooks and the intent notes")
    pg.click("summary:has-text('Comment-to-DM')"); t = pg.inner_text("details:has(summary:has-text('Comment-to-DM'))")
    ok("Hi @viewer, thank you for your comment." in t and "Sent! Check your messages" in t, "the exact DM and public reply can be read before approving")
    pg.goto(URL + "#/account/weone"); pg.wait_for_selector("h3:text-is('Comment-to-DM')", timeout=20000); pg.wait_for_timeout(800)
    lay = pg.evaluate("""() => { const a = [...document.querySelectorAll('article')].find((x) => x.querySelector('h3')?.textContent === 'Comment-to-DM');
      return [...a.querySelectorAll('.check-row label')].map((l) => [l.querySelector('input').getBoundingClientRect().width, l.querySelector('span').getBoundingClientRect().height]); }""")
    ok(len(lay) == 2 and all(w <= 24 and hgt <= 24 for w, hgt in lay), f"settings card: both switches are small checkboxes with one-line labels {lay}")
    tbl = pg.inner_text("table.kw-res")
    ok("MEDICAL" in tbl and "4.5 ★" in tbl and "2,000" in tbl, f"keyword results: 9 DMs from 2,000 reached = 4.5 per 1,000, best marked {tbl[:160]!r}")
    ok("FEES" in tbl and "too early" in tbl, "a reel younger than 2 days is not judged yet")
    ok(not errors, f"no page errors {errors[:2]}")
sql(f"delete from jobs where id='{jid}'"); sql("delete from dm_log where comment_id like 'kwt%'; delete from jobs where output->>'title' in ('KW test A','KW test B')")
bad = [n for k, n in R if not k]; print(f"{len(R) - len(bad)}/{len(R)} reel DM UI checks passed" + (" | FAILED: " + "; ".join(bad) if bad else ""))

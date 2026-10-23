"""Facebook Page Reels, worst cases: fb-auth (:8003) + poster (:8000) under Deno, cron simulator, fake_facebook.py (:8793).
    USER_JWT=... python fb_e2e.py
"""
import json, os, shutil, subprocess, sys, time, urllib.request, uuid
from pathlib import Path

BASE = "http://127.0.0.1:8765"; PG = "psql -h /tmp -p 5433 -U postgres -d rs_it"
FBD, STORE, IG = Path("/tmp/rs_stack/fb"), Path("/tmp/rs_stack/store"), Path("/tmp/rs_stack/ig")
SECRET = "0123456789abcdef0123456789abcdef"; UTOK = "EAAshort" + "u" * 80; IGTOK = "IGAAvalid" + "x" * 60
results = []


def sql(q): return subprocess.run(f"{PG} -tAc {json.dumps(q)}", shell=True, capture_output=True, text=True).stdout.strip()
def ok(c, n): results.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n, flush=True)
def scen(**kw):
    cur = json.loads(FBD.joinpath("scenario.json").read_text()) if FBD.joinpath("scenario.json").exists() else {}
    cur.update(kw); FBD.joinpath("scenario.json").write_text(json.dumps(cur))
def fbs(): return json.loads(FBD.joinpath("state.json").read_text()) if FBD.joinpath("state.json").exists() else {"videos": {}, "published": []}
def out(j): return json.loads(sql(f"select output from jobs where id='{j}'") or "{}")
def status(): return json.loads(sql("select value from settings where key='facebook_status'"))


def call(url, body, auth=True):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + os.environ["USER_JWT"]} if auth else {})})
    try: return json.loads(urllib.request.urlopen(req).read() or b"null")
    except urllib.error.HTTPError as e: return {"http": e.code, **json.loads(e.read() or b"{}")}
def rpc(fn, **a): return call(f"{BASE}/rest/v1/rpc/{fn}", a)
def auth(**a): return call("http://127.0.0.1:8003/", {"app_id": "1234567890123", "app_secret": SECRET, "user_token": UTOK, **a})


def wait(f, t, step=1.0):
    t0 = time.time()
    while time.time() - t0 < t:
        v = f()
        if v: return v
        time.sleep(step)
    return None


def ready(title):
    j = str(uuid.uuid4()); (STORE / j).mkdir(parents=True, exist_ok=True)
    for a, b in [("sample_reel.mp4", "reel.mp4"), ("sample_ig.mp4", "ig.mp4"), ("sample_thumb.jpg", "thumb.jpg")]: shutil.copy(f"/tmp/rs_stack/{a}", STORE / j / b)
    cap = f"{title}: plane par bijli giri? ⚡\n\nSource: FAA\n\nFollow @we_one_aviation for daily aviation facts\n\n#lightningstrike #aviationfacts #pilottraining #indianaviation #weoneaviation"
    o = {"title": title, "video": f"{j}/reel.mp4", "ig_video": f"{j}/ig.mp4", "ig_caption": cap, "thumb": f"{j}/thumb.jpg", "duration": 12}
    sql(f"insert into jobs(id,kind,status,stage,input,output) values ('{j}','reel','running','publish','{{}}','{json.dumps(o).replace(chr(39), chr(39) * 2)}')")
    sql(f"update jobs set status='review' where id='{j}'")
    return j


sql("update settings set value='{\"state\":\"not_connected\"}' where key in ('linkedin_status','facebook_status')")
IG.joinpath("scenario.json").write_text(json.dumps({"valid_tokens": [IGTOK]})); rpc("set_instagram_token", p_token=IGTOK)
scen(user_tokens=[UTOK])
# F0 connect
ok("not the Instagram app ID" in json.dumps(auth(app_id="abc")), "F0 wrong App ID format -> explains which ID to use")
ok("did not accept" in json.dumps(auth(app_secret="f" * 32)), "F0 wrong App Secret -> clear message")
ok(call("http://127.0.0.1:8003/", {}, auth=False).get("http") == 403, "F0 only the signed-in owner can connect")
r = auth()
ok(r.get("ok") and status().get("page_id") == "222" and status().get("page_name") == "We One Aviation", "F0 connected to the We One Aviation page (not the other page)")
ok(sql("select count(*) from secrets where value like '%" + SECRET + "%' or value like '%" + UTOK[:20] + "%'") == "0", "F0 App Secret and user token are not stored (only the page token)")
# F1 approve -> Instagram + Facebook
a = ready("Reel FA"); rpc("set_review_decision", p_job=a, p_decision="approve")
ok(wait(lambda: out(a).get("fb", {}).get("state") == "posted" and out(a).get("ig", {}).get("state") == "posted", 180), "F1 approve -> posted on Instagram and the Facebook Page")
v = fbs()["videos"][out(a)["fb"]["video_id"]]
ok(v["bytes"] > 100000 and "Follow We One Aviation" in v["description"] and "@" not in v["description"] and "#weoneaviation" in v["description"], "F1 Facebook got the real file and a Facebook caption (no Instagram @handle)")
ok(out(a)["fb"].get("permalink", "").startswith("https://www.facebook.com/reel/"), f"F1 link saved: {out(a)['fb'].get('permalink')}")
# F2 upload response lost -> checked, not repeated; one Reel
n0 = len(fbs()["published"]); b = ready("Reel FB"); scen(lose_upload_response=True); rpc("set_review_decision", p_job=b, p_decision="approve")
wait(lambda: out(b).get("fb", {}).get("error") or out(b).get("fb", {}).get("state") == "posted", 150)
if out(b)["fb"]["state"] != "posted": rpc("set_post", p_job=b, p_platform="fb", p_action="now")
ok(wait(lambda: out(b).get("fb", {}).get("state") == "posted", 150) and len(fbs()["published"]) == n0 + 1, "F2 lost upload response -> exactly one Facebook Reel")
# F3 page access removed -> waits with message; reconnect -> posts
c = ready("Reel FC"); scen(revoked=["PAGE_WOA" + "y" * 60]); rpc("set_review_decision", p_job=c, p_decision="approve")
ok(wait(lambda: status().get("state") == "expired" and "connected again" in (out(c).get("fb", {}).get("error") or ""), 150)
   and wait(lambda: out(c).get("ig", {}).get("state") == "posted", 120), "F3 Facebook access removed -> Facebook waits with a message, Instagram still posts")
scen(revoked=[]); auth(); rpc("set_post", p_job=c, p_platform="fb", p_action="now")
ok(wait(lambda: out(c).get("fb", {}).get("state") == "posted", 150), "F3 reconnect -> posted to Facebook")
# F4 per-platform control
d = ready("Reel FD"); rpc("hold_auto_post", p_job=d, p_hold=True); rpc("set_review_decision", p_job=d, p_decision="approve"); rpc("set_post", p_job=d, p_platform="fb", p_action="off")
ok(wait(lambda: out(d).get("ig", {}).get("state") == "posted", 120) and out(d)["fb"]["state"] == "off", "F4 'Don't post' on Facebook only -> Instagram posts, Facebook doesn't")
ok("already on Facebook" in json.dumps(rpc("set_post", p_job=a, p_platform="fb", p_action="now")), "F4 a reel already on Facebook cannot be posted twice")
bad = [n for k, n in results if not k]
print(f"{len(results) - len(bad)}/{len(results)} Facebook checks passed" + (" | FAILED: " + "; ".join(bad) if bad else ""))
sys.exit(1 if bad else 0)

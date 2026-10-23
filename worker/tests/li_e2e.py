"""LinkedIn Company Page posting, worst cases: linkedin-auth function (:8001) and the poster (:8000, Instagram + LinkedIn) under Deno,
cron simulator, local Postgres/PostgREST, fake_instagram.py (:8790) and fake_linkedin.py (:8792).   USER_JWT=... python li_e2e.py
"""
import json, os, re, shutil, subprocess, sys, time, urllib.request, uuid
from pathlib import Path

BASE = "http://127.0.0.1:8765"; PG = "psql -h /tmp -p 5433 -U postgres -d rs_it"
IG, LI, STORE = Path("/tmp/rs_stack/ig"), Path("/tmp/rs_stack/li"), Path("/tmp/rs_stack/store")
results = []
IGTOK = "IGAAvalid" + "x" * 60


def sql(q): return subprocess.run(f"{PG} -tAc {json.dumps(q)}", shell=True, capture_output=True, text=True).stdout.strip()
def ok(c, n): results.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n, flush=True)
def lisc(**kw):
    cur = json.loads(LI.joinpath("scenario.json").read_text()) if LI.joinpath("scenario.json").exists() else {}
    cur.update(kw); LI.joinpath("scenario.json").write_text(json.dumps(cur))
def lis(): return json.loads(LI.joinpath("state.json").read_text()) if LI.joinpath("state.json").exists() else {"posts": [], "videos": {}}
def out(j): return json.loads(sql(f"select output from jobs where id='{j}'") or "{}")
def status(): return json.loads(sql("select value from settings where key='linkedin_status'"))


def call(url, body, auth=True):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + os.environ["USER_JWT"]} if auth else {})})
    try: return json.loads(urllib.request.urlopen(req).read() or b"null")
    except urllib.error.HTTPError as e: return {"http": e.code, **json.loads(e.read() or b"{}")}


def rpc(fn, **a): return call(f"{BASE}/rest/v1/rpc/{fn}", a)


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
    cap = (f"{title}: plane par bijli giri (sach mein)? ⚡\n\n*Har* airliner ko saal mein ~1 baar [FAA] <data> pilot_training ~ #1 fact\n\n"
           "Follow @weoneaviation for daily aviation facts\n\n#lightningstrike #aviationfacts #pilottraining #indianaviation #weoneaviation")
    o = {"title": title, "video": f"{j}/reel.mp4", "ig_video": f"{j}/ig.mp4", "ig_caption": cap, "thumb": f"{j}/thumb.jpg", "duration": 12}
    sql(f"insert into jobs(id,kind,status,stage,input,output) values ('{j}','reel','running','publish','{{}}','{json.dumps(o).replace(chr(39), chr(39) * 2)}')")
    sql(f"update jobs set status='review' where id='{j}'")
    return j


IG.joinpath("scenario.json").write_text(json.dumps({"valid_tokens": [IGTOK]})); rpc("set_instagram_token", p_token=IGTOK)
# L0 before LinkedIn is set up, approval posts to Instagram only
a = ready("Reel A"); rpc("set_review_decision", p_job=a, p_decision="approve")
ok("li" not in out(a) and wait(lambda: out(a).get("ig", {}).get("state") == "posted", 120), "L0 LinkedIn not set up -> Instagram only, nothing waits for LinkedIn")
# L1 connect: wrong secret, then right; picks the We One Aviation page among two admin pages; secret never readable
ok("Client Secret looks wrong" in json.dumps(rpc("set_linkedin_app", p_client_id="86u8tzwdiyftvl", p_client_secret="x")), "L1 a malformed secret is refused with a reason")
rpc("set_linkedin_app", p_client_id="86u8tzwdiyftvl", p_client_secret="wrongSECRET99")
r = call("http://127.0.0.1:8001/", {"code": "goodcode", "redirect_uri": "https://weone-reelstudio.vercel.app/"})
ok("did not accept the sign-in" in json.dumps(r), "L1 wrong secret -> clear message from LinkedIn sign-in")
ok(call("http://127.0.0.1:8001/", {"code": "goodcode", "redirect_uri": "https://x/"}, auth=False).get("http") == 403, "L1 nobody but the signed-in owner can finish the connection")
rpc("set_linkedin_app", p_client_id="86u8tzwdiyftvl", p_client_secret="secretABC123xyz")
r = call("http://127.0.0.1:8001/", {"code": "goodcode", "redirect_uri": "https://weone-reelstudio.vercel.app/"})
st = status()
ok(r.get("ok") and st.get("state") == "connected" and st.get("org_urn") == "urn:li:organization:222" and st.get("vanity") == "we-one-aviation",
   f"L1 connected to the We One Aviation page (not the other admin page): {st.get('org_name')}, sign-in valid till {st.get('expires_at', '')[:10]}")
req = urllib.request.Request(f"{BASE}/rest/v1/secrets?select=key,value", headers={"Authorization": "Bearer " + os.environ["USER_JWT"]})
try: urllib.request.urlopen(req); readable = True
except urllib.error.HTTPError: readable = False
ok(not readable, "L1 the LinkedIn token and secret cannot be read through the site's API")
# L2 approve -> Instagram AND LinkedIn; video goes up in parts, LinkedIn processes it, post is published as the page
b = ready("Reel B"); rpc("set_review_decision", p_job=b, p_decision="approve")
ok(wait(lambda: out(b).get("li", {}).get("state") == "posted" and out(b).get("ig", {}).get("state") == "posted", 240), "L2 approve -> posted on Instagram and the LinkedIn page")
s = lis(); post = s["posts"][-1]; vid = s["videos"][post["content"]["media"]["id"]]
ok(len(vid["parts"]) >= 2 and vid["owner"] == "urn:li:organization:222", f"L2 video uploaded in {len(vid['parts'])} parts as the page")
c = post["commentary"]
ok("#weoneaviation" in c and "\\(" in c and "\\*" in c and "@" not in c and "Follow We One Aviation" in c, "L2 caption converted to LinkedIn text: hashtags kept, special characters escaped, Instagram @handle line replaced")
ok(out(b)["li"].get("permalink", "").startswith("https://www.linkedin.com/feed/update/urn:li:share:"), f"L2 link saved: {out(b)['li'].get('permalink')}")
# L3 an upload part fails once -> resumes from the saved part (no new upload session)
n0 = len(lis()["videos"]); cc = ready("Reel C"); lisc(part_500_once=True); rpc("set_review_decision", p_job=cc, p_decision="approve")
wait(lambda: out(cc).get("li", {}).get("error"), 120)
rpc("set_post", p_job=cc, p_platform="li", p_action="now")
ok(wait(lambda: out(cc).get("li", {}).get("state") == "posted", 240) and len(lis()["videos"]) == n0 + 1, "L3 failed upload part -> resumed and posted, same upload session")
# L4 response to 'create post' lost -> recognised, exactly one post
d = ready("Reel D"); lisc(lose_post_response=True); rpc("set_review_decision", p_job=d, p_decision="approve")
wait(lambda: out(d).get("li", {}).get("error") or out(d).get("li", {}).get("state") == "posted", 240)
if out(d)["li"]["state"] != "posted": rpc("set_post", p_job=d, p_platform="li", p_action="now")
ok(wait(lambda: out(d).get("li", {}).get("state") == "posted", 240) and len([p for p in lis()["posts"] if p["commentary"].startswith("Reel D")]) == 1, "L4 lost post response -> exactly one LinkedIn post")
# L5 sign-in revoked -> paused with message; Instagram unaffected; after reconnect it posts
e = ready("Reel E"); lisc(valid_tokens=[]); rpc("set_review_decision", p_job=e, p_decision="approve")
ok(wait(lambda: status().get("state") == "expired" and "connected again" in (out(e).get("li", {}).get("error") or ""), 240)
   and wait(lambda: out(e).get("ig", {}).get("state") == "posted", 120), "L5 LinkedIn sign-in revoked -> LinkedIn waits with a message, Instagram still posts")
call("http://127.0.0.1:8001/", {"code": "goodcode", "redirect_uri": "https://weone-reelstudio.vercel.app/"}); rpc("set_post", p_job=e, p_platform="li", p_action="now")
ok(wait(lambda: out(e).get("li", {}).get("state") == "posted", 240), "L5 reconnect -> posted to LinkedIn")
# L6 per-platform control
f = ready("Reel F"); rpc("hold_auto_post", p_job=f, p_hold=True); rpc("set_review_decision", p_job=f, p_decision="approve")
rpc("set_post", p_job=f, p_platform="li", p_action="off")
ok(wait(lambda: out(f).get("ig", {}).get("state") == "posted", 120) and out(f)["li"]["state"] == "off", "L6 'Don't post' on LinkedIn only -> Instagram posts, LinkedIn doesn't")
ok("already on LinkedIn" in json.dumps(rpc("set_post", p_job=b, p_platform="li", p_action="now")), "L6 a reel already on LinkedIn cannot be posted twice")
bad = [n for k, n in results if not k]
print(f"{len(results) - len(bad)}/{len(results)} LinkedIn checks passed" + (" | FAILED: " + "; ".join(bad) if bad else ""))
sys.exit(1 if bad else 0)

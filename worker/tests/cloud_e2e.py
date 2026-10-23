"""Cloud auto-posting worst cases: real worker (prepares caption + Instagram file), local Postgres/PostgREST, the ig-poster
Edge Function under Deno, a cron simulator (auto_approve_due + poster every 5 s) and fake_instagram.py.
    USER_JWT=... WORKER_PID=<pid> python cloud_e2e.py
"""
import json, os, re, shutil, signal, subprocess, sys, time, urllib.request, uuid
from pathlib import Path

BASE = "http://127.0.0.1:8765"; PG = "psql -h /tmp -p 5433 -U postgres -d rs_it"
IG = Path("/tmp/rs_stack/ig"); STORE = Path("/tmp/rs_stack/store")
VALID = "IGAAvalid" + "x" * 60
results = []


def sql(q): return subprocess.run(f"{PG} -tAc {json.dumps(q)}", shell=True, capture_output=True, text=True).stdout.strip()
def ok(c, n): results.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n, flush=True)
def scen(**kw): IG.joinpath("scenario.json").write_text(json.dumps(kw))
def igs(): return json.loads(IG.joinpath("state.json").read_text()) if IG.joinpath("state.json").exists() else {"media": [], "containers": {}}
def out(j): return json.loads(sql(f"select output from jobs where id='{j}'") or "{}")
def st(j): return sql(f"select status from jobs where id='{j}'")


def rpc(fn, **a):
    req = urllib.request.Request(f"{BASE}/rest/v1/rpc/{fn}", data=json.dumps(a).encode(), method="POST",
                                 headers={"Authorization": "Bearer " + os.environ["USER_JWT"], "Content-Type": "application/json"})
    try: return json.loads(urllib.request.urlopen(req).read() or b"null")
    except urllib.error.HTTPError as e: return {"error": json.loads(e.read())}


def wait(f, t, step=1.0):
    t0 = time.time()
    while time.time() - t0 < t:
        v = f()
        if v: return v
        time.sleep(step)
    return None


IGFILE = Path("/tmp/rs_stack/sample_ig.mp4")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", "/tmp/rs_stack/sample_reel.mp4", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-movflags", "+faststart", str(IGFILE)], check=True)


def ready_reel(title, prepared=True):
    """A reel that has just finished on the Mac: files in storage (+ the Instagram caption/file the Mac prepares at publish), status -> review."""
    j = str(uuid.uuid4()); (STORE / j).mkdir(parents=True, exist_ok=True)
    shutil.copy("/tmp/rs_stack/sample_reel.mp4", STORE / j / "reel.mp4"); shutil.copy("/tmp/rs_stack/sample_thumb.jpg", STORE / j / "thumb.jpg")
    o = {"title": title, "video": f"{j}/reel.mp4", "thumb": f"{j}/thumb.jpg", "duration": 12,
         "claims": [{"claim": "Airliners are struck by lightning about once a year", "source_url": "https://www.faa.gov/x"}], "script": [{"t": 0, "text": "Plane par *bijli*"}]}
    if prepared:
        shutil.copy(IGFILE, STORE / j / "ig.mp4")
        o.update(ig_video=f"{j}/ig.mp4", ig_caption=f"{title}: plane par bijli giri, phir bhi sab safe? ⚡\n\nSource: FAA\n\n#lightningstrike #aviationfacts #pilottraining #indianaviation #weoneaviation")
    sql(f"insert into jobs(id,kind,status,stage,input,output) values ('{j}','reel','running','publish','{{}}','{json.dumps(o).replace(chr(39), chr(39) * 2)}')")
    sql(f"update jobs set status='review' where id='{j}'")
    return j


scen(valid_tokens=[VALID]); rpc("set_instagram_token", p_token=VALID); rpc("set_autopost", p_auto=True, p_minutes=1)
ok(wait(lambda: json.loads(sql("select value from settings where key='instagram_status'")).get("state") == "connected", 40), "C0 cloud poster connects to Instagram")
# C1 the Mac prepares the Instagram caption (<= 5 hashtags) and an Instagram-spec file as soon as a reel is ready
a = ready_reel("Reel A", prepared=False)
o = wait(lambda: (lambda x: x if x.get("ig_caption") and x.get("ig_video") else None)(out(a)), 60)
tags = re.findall(r"#\w+", (o or {}).get("ig_caption", ""))
ok(o and 3 <= len(tags) <= 5 and "#weoneaviation" in tags, f"C1 Mac prepared caption ({len(tags)} hashtags, Instagram's limit is 5) and the Instagram file")
# C2 THE MAC IS SWITCHED OFF; nobody answers -> after the review window the cloud approves and posts it
os.kill(int(os.environ["WORKER_PID"]), signal.SIGKILL); time.sleep(1)
ok(st(a) == "review", "C2 Mac off; reel waits in Review for the window")
ok(wait(lambda: out(a).get("ig", {}).get("state") == "posted", 150), "C2 no answer within the window -> auto-approved and posted to Instagram with the Mac OFF")
oa = out(a); c = next((c for c in igs()["containers"].values() if c["caption"].startswith(oa.get("ig_caption", "")[:40])), {})
ok(oa.get("decision") == "auto" and oa["ig"].get("permalink"), f"C2 recorded as auto-approved, link {oa['ig'].get('permalink')}")
ok(c and int(c["video"]["audio_bitrate"]) <= 160000 and c.get("share_to_feed") == "true", "C2 Instagram got the prepared file (128 kbps audio) and the 5-hashtag caption")
# C3 Approve within the window -> posts at once (cloud, Mac still off; file prepared earlier is not needed: fallback path)
b = ready_reel("Reel B"); rpc("set_review_decision", p_job=b, p_decision="approve")
ok(wait(lambda: out(b).get("ig", {}).get("state") == "posted", 150), "C3 Approve -> posted right away (Mac off)")
# C4 "Wait, I'm checking" stops the timer; Request changes never posts
cc = ready_reel("Reel C"); d = ready_reel("Reel D")
rpc("hold_auto_post", p_job=cc, p_hold=True); rpc("request_changes", p_job=d, p_notes="shorter hook")
time.sleep(80)
ok(st(cc) == "review" and not out(cc).get("ig"), "C4 paused reel is not posted after the window")
ok(st(d) == "cancelled" and len([m for m in igs()["media"] if "Reel D" in m["caption"]]) == 0, "C4 Request changes -> not posted")
rpc("hold_auto_post", p_job=cc, p_hold=False)
ok(wait(lambda: out(cc).get("ig", {}).get("state") == "posted", 160), "C4 Restart timer -> posts after a fresh window")
# C5 publish response lost -> no duplicate
n0 = len(igs()["media"]); e = ready_reel("Reel E"); scen(valid_tokens=[VALID], crash_after_publish=True)
rpc("set_review_decision", p_job=e, p_decision="approve")
wait(lambda: out(e).get("ig", {}).get("error") or out(e).get("ig", {}).get("state") == "posted", 150)
if out(e)["ig"]["state"] != "posted": rpc("set_reel_posting", p_job=e, p_action="now")
ok(wait(lambda: out(e).get("ig", {}).get("state") == "posted", 150) and len(igs()["media"]) == n0 + 1, "C5 lost publish response -> posted exactly once")
# C6 token revoked -> waits with a message, posts after reconnect
f = ready_reel("Reel F"); scen(valid_tokens=[]); rpc("set_review_decision", p_job=f, p_decision="approve")
ok(wait(lambda: "connected again" in (out(f).get("ig", {}).get("error") or ""), 120), "C6 revoked token -> reel waits, 'connect again' shown")
NEW = "IGAAnew" + "z" * 60; scen(valid_tokens=[NEW]); rpc("set_instagram_token", p_token=NEW); rpc("set_reel_posting", p_job=f, p_action="now")
ok(wait(lambda: out(f).get("ig", {}).get("state") == "posted", 150), "C6 reconnect -> posted")
# C7 two poster calls at the same moment never double-post
g = ready_reel("Reel G"); rpc("set_review_decision", p_job=g, p_decision="approve")
key = sql("select value from secrets where key='cron_key'")
for _ in range(3):
    subprocess.Popen(["curl", "-s", "-X", "POST", "-H", f"x-cron-key: {key}", "localhost:8000/"], stdout=subprocess.DEVNULL)
ok(wait(lambda: out(g).get("ig", {}).get("state") == "posted", 150) and len([m for m in igs()["media"] if m["caption"].startswith(out(g).get("ig_caption", "#")[:30])]) <= 1, "C7 parallel poster calls -> one post")
bad = [n for k, n in results if not k]
print(f"{len(results) - len(bad)}/{len(results)} cloud auto-post checks passed" + (" | FAILED: " + "; ".join(bad) if bad else ""))
sys.exit(1 if bad else 0)

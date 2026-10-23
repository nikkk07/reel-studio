"""Instagram auto-posting, worst cases, against the real worker + local Postgres/PostgREST + fake_instagram.py.
    USER_JWT=... python ig_e2e.py     (stack from web/tests/worst_cases.py, fake IG on :8790 with state in /tmp/rs_stack/ig)
"""
import json, os, shutil, subprocess, sys, time, urllib.request, uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = "http://127.0.0.1:8765"; PG = "psql -h /tmp -p 5433 -U postgres -d rs_it"
IG = Path("/tmp/rs_stack/ig"); STORE = Path("/tmp/rs_stack/store"); JOBS = Path("/home/claude/rs_test2/jobs")
VALID = "IGAAvalid" + "x" * 60
results = []


def sql(q): return subprocess.run(f"{PG} -tAc {json.dumps(q)}", shell=True, capture_output=True, text=True).stdout.strip()
def ok(c, n): results.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n, flush=True)
def scen(**kw): IG.joinpath("scenario.json").write_text(json.dumps(kw))
def igstate(): return json.loads(IG.joinpath("state.json").read_text()) if IG.joinpath("state.json").exists() else {"media": [], "containers": {}}
def ig(jid): return json.loads(sql(f"select coalesce(output->'ig','{{}}') from jobs where id='{jid}'") or "{}")
def status(): return json.loads(sql("select value from settings where key='instagram_status'"))


def rpc(fn, **args):
    req = urllib.request.Request(f"{BASE}/rest/v1/rpc/{fn}", data=json.dumps(args).encode(), method="POST",
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


def reel(title, local=False):
    jid = str(uuid.uuid4()); (STORE / jid).mkdir(parents=True, exist_ok=True)
    shutil.copy("/tmp/rs_stack/sample_reel.mp4", STORE / jid / "reel.mp4"); shutil.copy("/tmp/rs_stack/sample_thumb.jpg", STORE / jid / "thumb.jpg")
    if local:
        (JOBS / jid / "render").mkdir(parents=True, exist_ok=True); shutil.copy("/tmp/rs_stack/sample_reel.mp4", JOBS / jid / "render" / "reel.mp4")
    out = {"title": title, "video": f"{jid}/reel.mp4", "thumb": f"{jid}/thumb.jpg", "duration": 12,
           "claims": [{"claim": "Airliners are struck by lightning about once a year", "source_url": "https://www.faa.gov/x"}],
           "script": [{"t": 0, "text": "Plane par *bijli* giri"}]}
    sql(f"insert into jobs(id,kind,status,stage,input,output) values ('{jid}','reel','review','finished','{{}}','{json.dumps(out)}')")
    return jid


scen(valid_tokens=[VALID])
# I1 approve without Instagram connected: gets the next 19:30 IST slot, nothing posts
a = reel("Reel A", local=True); rpc("set_review_decision", p_job=a, p_decision="approve")
s = wait(lambda: ig(a).get("state") == "scheduled" and ig(a), 20)
tz = ZoneInfo("Asia/Kolkata"); now = datetime.now(tz)
exp = now.replace(hour=19, minute=30, second=0, microsecond=0); exp = exp if exp > now + timedelta(minutes=3) else exp + timedelta(days=1)
ok(s and datetime.fromisoformat(s["at"].replace("Z", "+00:00")) == exp, f"I1 approve -> scheduled for the next 7:30 PM IST ({exp:%d %b %H:%M})")
b = reel("Reel B"); rpc("set_review_decision", p_job=b, p_decision="approve")
sb_ = wait(lambda: ig(b).get("state") == "scheduled" and ig(b), 20)
ok(sb_ and sb_["at"] != s["at"], "I1 a second approved reel gets the following slot, never the same one")
# I2 wrong / expired token
r = rpc("set_instagram_token", p_token="IGAAwrong" + "y" * 60)
ok(wait(lambda: status()["state"] == "expired", 30), "I2 a wrong token is reported as 'connection expired' with a reason: " + status().get("note", "")[:60])
# I3 correct token
rpc("set_instagram_token", p_token=VALID)
ok(wait(lambda: status()["state"] == "connected", 30) and status()["username"] == "weoneaviation", "I3 connected as @weoneaviation")
ok(VALID not in open("/home/claude/rs_test2/logs/worker.log").read() and VALID not in open("/tmp/rs_stack/worker.out").read(), "I3 token never appears in any log")
# I4 Post now: real file goes through Instagram's spec checks, one post with caption, hashtags and cover
rpc("set_reel_posting", p_job=a, p_action="now")
done = wait(lambda: ig(a).get("state") in ("posted", "failed") and ig(a), 120)
st = igstate(); c = next(iter(st["containers"].values()), {})
ok(done and done["state"] == "posted" and done.get("permalink", "").startswith("https://www.instagram.com/reel/"), f"I4 Post now -> on Instagram {done and done.get('permalink')}")
ok(c.get("video", {}).get("audio_bitrate") and int(c["video"]["audio_bitrate"]) <= 160000 and c.get("share_to_feed") == "true", "I4 video passed Instagram's Reels spec (320k audio re-encoded to 128k, moov first) and shares to feed")
cap = c.get("caption", "")
ok("#weoneaviation" in cap and cap.count("#") <= 30 and len(cap) <= 2200 and "Source" in cap, "I4 caption: hook, facts, source line, hashtags, within limits")
ok(len(st["media"]) == 1, "I4 exactly one post")
# I5 Instagram rejects processing once -> automatic retry later; Retry now posts it
scen(valid_tokens=[VALID], container_error_once=True); rpc("set_reel_posting", p_job=b, p_action="now")
r5 = wait(lambda: ig(b).get("error") and ig(b).get("state") == "scheduled" and ig(b), 120)
ok(r5 and datetime.fromisoformat(r5["at"].replace("Z", "+00:00")) > datetime.now(tz) + timedelta(minutes=8), "I5 processing error -> retry scheduled ~10 min later, reason shown: " + (r5 or {}).get("error", "")[:60])
rpc("set_reel_posting", p_job=b, p_action="now")
ok(wait(lambda: ig(b).get("state") == "posted", 120), "I5 Retry now -> posted")
# I6 the response to 'publish' is lost (Mac crash / network) -> recognised as already published, no duplicate post
c6 = reel("Reel C"); rpc("set_review_decision", p_job=c6, p_decision="approve"); wait(lambda: ig(c6).get("state") == "scheduled", 20)
scen(valid_tokens=[VALID], crash_after_publish=True); n0 = len(igstate()["media"]); rpc("set_reel_posting", p_job=c6, p_action="now")
wait(lambda: ig(c6).get("error") or ig(c6).get("state") == "posted", 120); rpc("set_reel_posting", p_job=c6, p_action="now")
ok(wait(lambda: ig(c6).get("state") == "posted", 120) and len(igstate()["media"]) == n0 + 1, "I6 lost publish response -> marked posted, still exactly one post")
# I7 token revoked while reels wait -> posting pauses with a clear message, resumes after reconnect
d = reel("Reel D"); rpc("set_review_decision", p_job=d, p_decision="approve"); wait(lambda: ig(d).get("state") == "scheduled", 20)
scen(valid_tokens=[]); rpc("set_reel_posting", p_job=d, p_action="now")
ok(wait(lambda: status()["state"] == "expired", 60) and wait(lambda: ig(d).get("state") == "scheduled" and "connected again" in (ig(d).get("error") or ""), 60), "I7 revoked token -> paused, 'connect again' shown, reel kept")
NEW = "IGAAnew" + "z" * 60; scen(valid_tokens=[NEW]); rpc("set_instagram_token", p_token=NEW); rpc("set_reel_posting", p_job=d, p_action="now")
ok(wait(lambda: ig(d).get("state") == "posted", 120), "I7 reconnect -> the waiting reel posts")
# I8 token renewal before 60 days
sql("update secrets set updated_at = now() - interval '4 days' where key='instagram_token'")
sql("update settings set value = value || '{\"checked_at\": \"2020-01-01T00:00:00Z\"}' where key='instagram_status'")
ok(wait(lambda: sql("select value like 'IGAArefreshed%' from secrets where key='instagram_token'") == "t", 60) and status().get("expires_at"), "I8 token renewed automatically, new expiry recorded")
# I9 site controls on a posted reel
e = rpc("set_reel_posting", p_job=a, p_action="now")
ok("already on Instagram" in json.dumps(e), "I9 a posted reel cannot be posted again")
# I10 Mac was off at posting time: a slot missed by hours moves to the next slot (no 3 AM post)
m = reel("Reel M"); sql(f"update jobs set status='done', output = output || '{{\"ig\":{{\"state\":\"scheduled\",\"at\":\"2020-01-01T14:00:00Z\",\"attempts\":0}}}}' where id='{m}'")
r10 = wait(lambda: ig(m).get("state") == "scheduled" and ig(m).get("missed") and ig(m), 30)
ok(r10 and datetime.fromisoformat(r10["at"].replace("Z", "+00:00")) > datetime.now(tz), "I10 missed slot -> moved to the next posting time")
bad = [n for g, n in results if not g]
print(f"{len(results) - len(bad)}/{len(results)} Instagram checks passed" + (" | FAILED: " + "; ".join(bad) if bad else ""))
sys.exit(1 if bad else 0)

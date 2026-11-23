"""Data guard on the Mac (Supabase egress): the meter, the guard level, light reads for the CRM report and the autopilot
history.   python tests/egress_test.py   (stand-in HTTP, fake DB, no network)"""
import copy, json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent)); import pgsel  # noqa: E402
os.environ.setdefault("REELSTUDIO_APP_DIR", tempfile.mkdtemp())
import httpx  # noqa: E402
from reelworker import db as dbmod, crm  # noqa: E402
from reelworker.jobs import autopilot  # noqa: E402

res = []
def ok(c, n): res.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n)

# ---------------------------------------------------------------- meter + level
calls = []; LEVEL = {"v": 1}; MISSING = {"v": False}
def handler(req: httpx.Request):
    calls.append((req.method, req.url.path, req.headers.get("prefer"), req.content.decode() if req.content else "", str(req.url)))
    if req.url.path.endswith("/rpc/add_egress"):
        if MISSING["v"]: return httpx.Response(404, json={"code": "PGRST202", "message": "Could not find the function"})
        return httpx.Response(200, json={"used": 10, "budget": 100, "reqs": 3, "level": LEVEL["v"]})
    if req.url.path.endswith("/workers") or req.url.path.endswith("/job_events") or req.url.path.endswith("/settings"): return httpx.Response(201)
    if req.method == "PATCH": return httpx.Response(200, json=[{"id": "a"}])
    return httpx.Response(200, json=[{"id": "a", "output": {"script": "x" * 3000}}])
sb = dbmod.Supabase(url="http://sb.test", key="svc")
sb.c = httpx.Client(transport=httpx.MockTransport(handler))
sb.select("jobs", "select=id")
ok(sb.meter["reqs"] == 1 and sb.meter["bytes"] >= 400 + 3000, f"every response is counted ({sb.meter})")
r = sb.insert("workers", {"id": "mac"}, upsert=True, minimal=True)
ok(r is None and "return=minimal" in calls[-1][2] and "merge-duplicates" in calls[-1][2], "the heartbeat asks for nothing back")
sb.update("jobs", "id=eq.a&status=eq.running", {"progress": 0.5})
ok(calls[-1][0] == "PATCH" and calls[-1][4].endswith("&select=id"), "a progress save asks back only the id, never the whole reel")
sb.insert("job_events", {"job_id": "a", "message": "x"}); ok("return=minimal" in calls[-1][2], "a log line is written without a reply")
sb.insert("settings", {"key": "digest", "value": {}}, upsert=True); ok("return=minimal" in calls[-1][2], "a saved digest comes back empty")
before = dict(sb.meter)
g = sb.flush_meter()
body = json.loads(calls[-1][3])
ok(body["p_part"] == "mac" and body["p_bytes"] == before["bytes"] and body["p_reqs"] == before["reqs"], "the count is added as the Mac's part")
ok(g["level"] == 1 and sb.slow == 2, "saver level: background checks run half as often")
LEVEL["v"] = 2; sb.flush_meter(); ok(sb.slow == 4, "strict level: a quarter as often")
MISSING["v"] = True; n0 = len(calls); sb.select("jobs", "select=id"); kept = sb.meter["reqs"]; sb.flush_meter()
ok(sb.meter["reqs"] >= kept and sb.slow == 4, "not installed yet: the count is kept, the last level stays")
n1 = len(calls); sb.flush_meter(); ok(len(calls) == n1, "and it is not asked again for an hour")

# ---------------------------------------------------------------- CRM report: light rows, and nothing read when nothing changed
class DB:
    def __init__(self, jobs): self.jobs = jobs; self.q = []; self.stamp = "2026-11-23T05:00:00Z"
    def select(self, t, q=""):
        self.q.append(q)
        rows = [j for j in self.jobs if (j["input"].get("crm_item") if "crm_item=not.is.null" in q else ((j.get("output") or {}).get("ig") or {}).get("via") == "crm")]
        rows = [{**j, "updated_at": self.stamp} for j in rows]
        return pgsel.project(rows, q)
BIG = {"script": "s" * 20000, "critic": {"total": 80, "notes": "n" * 5000}}
jobs = [{"id": "j1", "kind": "reel", "status": "done", "error": None, "created_at": "2026-11-20T00:00:00Z", "input": {"crm_item": 7},
         "output": {**BIG, "ig": {"state": "posted", "posted_at": "2026-11-21T10:00:00Z", "permalink": "https://www.instagram.com/reel/x"}, "ig_stats": {"views": 900, "saved": 4}}},
        {"id": "j2", "kind": "reel", "status": "review", "error": None, "created_at": "2026-11-22T00:00:00Z", "input": {"crm_item": 8},
         "output": {**BIG, "autopilot_hold": True}}]
os.environ["REELSTUDIO_CRM_URL"] = "https://crm.test"
reports = []
crm.call = lambda action, **b: reports.append((action, b)) or {"ok": True}
(crm.config.APP / "state").mkdir(parents=True, exist_ok=True); (crm.config.APP / "state" / "crm_sent.json").write_text("{}")
want = {str(j["input"]["crm_item"]): crm.report_of(copy.deepcopy(j)) for j in jobs}
d = DB(jobs); crm._SYNC["sig"] = None
n = crm.sync(d)
got = {str(b["id"]): {k: v for k, v in b.items() if k != "id"} for a, b in reports if a == "update"}
ok(n == 2 and got == want, f"light rows give the CRM exactly the same report ({got})")
ok(all("output,error" not in q and "input,output" not in q for q in d.q), "no whole reel is read")
d.q.clear(); reports.clear()
ok(crm.sync(d) == 0 and len(d.q) == 2 and all(q.startswith("select=updated_at") for q in d.q), "nothing changed: two tiny reads, nothing else")
d.stamp = "2026-11-23T06:00:00Z"; jobs[1]["status"] = "done"; jobs[1]["output"]["ig"] = {"state": "scheduled", "at": "2026-11-24T08:00:00Z"}
d.q.clear(); crm.sync(d)
ok(len(d.q) == 4 and any(b["id"] == 8 for a, b in reports if a == "update"), "a changed job is reported on the next round")

# ---------------------------------------------------------------- autopilot history: light rows, same result
class HDB:
    def __init__(self, rows, light): self.rows, self.light = rows, light
    def select(self, t, q=""):
        if t != "jobs": return []
        return pgsel.project(self.rows, q) if self.light else copy.deepcopy(self.rows)
hrows = [{"id": "h1", "status": "done", "created_at": "2026-11-20T00:00:00Z",
          "input": {"concept": {"title": "CPL fees", "category": "career", "lane": "core", "hook": "₹50 lakh?"}, "autopilot": "ap1"},
          "output": {**BIG, "title": "CPL fees", "thumb": "h1/thumb.jpg", "duration": 41.5, "hook": {"say": "₹50 lakh?"}, "qc": {"score": 8, "issues": []},
                     "critic": {"total": 82, "words": 96, "summary": "ok"}, "ig": {"state": "posted", "posted_at": "2026-11-21T10:00:00Z", "media_id": "m1", "permalink": "https://i/x"},
                     "ig_stats": {"views": 1000, "reach": 800, "shares": 9, "saved": 5, "likes": 40, "comments": 3, "hours_since_post": 30, "top_comments": ["wow"]}}},
         {"id": "h2", "status": "review", "created_at": "2026-11-22T00:00:00Z", "input": {"category": "facts"}, "output": {"title": "Black box"}}]
light = autopilot.history(HDB(hrows, True), brand="weone")
h = next(x for x in light if x["id"] == "h1"); h2 = next(x for x in light if x["id"] == "h2")
ok(len(light) == 2 and h["title"] == "CPL fees" and h["category"] == "career" and h["hook"] == "₹50 lakh?" and h["autopilot"] is True
   and h["views"] == 1000 and h["saved"] == 5 and h["critic"] == 82 and h["words"] == 96 and h["qc"] == 8 and h["duration"] == 41.5
   and h["media_id"] == "m1" and h["permalink"] == "https://i/x" and h["said"] == ["wow"] and h["opening"] == {"say": "₹50 lakh?"}
   and h["thumb"] == "h1/thumb.jpg" and h["hours_live"] == 30 and h["eng"] is not None, f"history from light rows keeps every field ({h})")
ok(h2["title"] == "Black box" and h2["category"] == "facts" and h2["autopilot"] is False and h2["critic"] is None, "a reel without results or reviews")
q = autopilot.HIST_COLS
ok("output," not in q + "," and "input," not in q + "," and "critic->total" in q, "history never asks for the whole reel")

bad = [n for c, n in res if not c]
print(f"{len(res) - len(bad)}/{len(res)} data-guard checks passed" + (" | FAILED: " + "; ".join(bad) if bad else ""))
sys.exit(1 if bad else 0)

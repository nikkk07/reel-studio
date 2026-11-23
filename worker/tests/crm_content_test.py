"""CRM content plan (Reel Studio 0026): CRM items -> jobs, status reports back, carousel + ad picture making, slides, 3D library.
python tests/crm_content_test.py      (fake DB, scripted agents, no network; needs skia, ffmpeg and the reel fonts in REELSTUDIO_FONTS_DIR)"""
import json, os, subprocess, sys, tempfile, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
ROOT = tempfile.mkdtemp()
os.environ.update(REELSTUDIO_ROOT=ROOT, REELSTUDIO_BRAIN="mock", REELSTUDIO_3D_DIR=f"{ROOT}/3d", CRM_CONTENT_TOKEN="t" * 40)
os.environ.setdefault("REELSTUDIO_FONTS_DIR", "/tmp/rsfonts")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent)); import pgsel  # noqa: E402
from reelworker import config, crm, dm, agents  # noqa: E402
from reelworker.runner import JobContext  # noqa: E402
from reelworker.jobs import carousel, content  # noqa: E402
from reelworker.media import slides, library3d, footage  # noqa: E402
from PIL import Image  # noqa: E402

R = []
def ok(c, n): R.append((bool(c), n)); print(("PASS " if c else "FAIL ") + n, flush=True)
WP = json.load(open(Path(__file__).resolve().parents[2] / "brands/weone/profile.json"))
IST = timezone(timedelta(hours=5, minutes=30))


class DB:
    def __init__(self, jobs=()):
        self.jobs = list(jobs); self.ins = []; self.ups = []; self.files = {}
    def select(self, t, q=""):
        if t == "brands": return [{"id": "weone", "name": "We One Aviation", "website": "https://weoneaviation.in", "instagram": "we_one_aviation", "logo_asset": None, "profile": WP}]
        if t == "jobs" and "input->>crm_item=eq." in q:
            i = q.split("input->>crm_item=eq.")[1].split("&")[0]
            return pgsel.project([j for j in self.jobs if str(j["input"].get("crm_item")) == i and j["status"] not in ("failed", "cancelled")], q)
        if t == "jobs" and "crm_item=not.is.null" in q:
            return pgsel.project(sorted([j for j in self.jobs if j["input"].get("crm_item")], key=lambda j: j["created_at"]), q)
        return []
    def insert(self, t, row, upsert=False):
        row = {"id": str(uuid.uuid4()), **row}; self.ins.append((t, row))
        if t == "jobs": self.jobs.append({**row, "status": "queued", "created_at": datetime.now(timezone.utc).isoformat()})
        return [row]
    def update(self, t, m, v): self.ups.append((t, m, v)); return [{"id": "x"}]
    def upload(self, path, f, ctype): self.files[path] = (Path(f).read_bytes(), ctype)


class CRM:
    """Stands in for crm.call: a queue of items and the updates/pictures the Mac sends."""
    def __init__(self, items=()): self.items = list(items); self.sent = []
    def __call__(self, action, **b):
        self.sent.append((action, b))
        if action == "next": return {"item": self.items.pop(0) if self.items else None}
        if action == "picture": return {"ok": True, "ready": False}
        if action == "signals": return SIG
        return {"ok": True}


wk = datetime.now(IST).date()
SIG = {"demand": {"summary": "Fees after 12th lead.", "demand": [{"topic": "CPL cost", "why": "900 searches"}], "questions": ["How much is CPL?"], "gaps": ["No one shows costs"]},
       "this_week": [{"kind": "reel", "title": "CPL cost", "slot": f"{wk}T13:30:00+05:30", "status": "planned"}],
       "best": [{"title": "Old reel", "leads": 3}]}
ITEM = {"id": 11, "kind": "reel", "title": "CPL cost, line by line", "slot": "2026-11-24T08:00:00+00:00", "link": "https://weoneaviation.in/?utm_campaign=content_11",
        "brief": {"hook": "Where does the money go?", "angle": "cost", "persona": "parent", "outline": ["Ground classes", "Flying hours"],
                  "facts_to_verify": ["DGCA CPL needs 200 hours"], "evidence": [{"signal": "search", "detail": "cpl fees, 900 seen"}], "cta": "Save this"},
        "banned_claims": ["100% placement"], "persona": {"name": "Parent", "who": "Parents of class 12"}}

# ---------------------------------------------------------------- CRM item -> job
k, inp = crm.job_for(ITEM)
ok(k == "reel" and inp["crm_item"] == 11 and inp["post_at"] == ITEM["slot"] and inp["concept"]["hook"] == "Where does the money go?"
   and "DGCA CPL needs 200 hours" in inp["concept"]["key_facts"] and inp["banned_claims"] == ["100% placement"] and "100% placement" in inp["concept"]["risk_note"],
   "CR01 a planned reel becomes a reel job with its planned time, brief, facts to verify and banned claims")
k2, inp2 = crm.job_for({**ITEM, "kind": "carousel"}); k3, inp3 = crm.job_for({**ITEM, "kind": "ad_video", "brief": {"headline": "Fly in 18 months", "visual_brief": "cockpit"}})
k4, inp4 = crm.job_for({**ITEM, "kind": "ad_image", "brief": {"headline": "H", "sizes": ["1080x1350", "1080x1920"]}})
ok(k2 == "reel" and inp2["format"] == "carousel" and inp2["post_at"] and k3 == "reel" and inp3["ad"] and "post_at" not in inp3
   and inp3["concept"]["est_seconds"] == 30 and k4 == "content" and inp4["make"] == "ad_image" and inp4["sizes"] == ["1080x1350", "1080x1920"],
   "CR02 carousel -> slides job, ad video -> unposted reel, ad picture -> content job with both sizes")

db = DB(); fake = CRM([ITEM, {**ITEM, "id": 12, "kind": "carousel"}]); crm.call = fake
n = crm.take(db)
ok(n == 2 and len(db.jobs) == 2 and [a for a, _ in fake.sent].count("update") == 2 and fake.sent[1][1]["status"] == "producing"
   and fake.sent[1][1]["studio_job"] == db.jobs[0]["id"], "CR03 due items are queued here and reported as being made")
fake.items = [ITEM]; crm.take(db)
ok(len(db.jobs) == 2 and [b for a, b in fake.sent if a == "update"][-1]["studio_job"] == db.jobs[0]["id"], "CR04 an item handed out again is never made twice")

# ---------------------------------------------------------------- job -> CRM status
J = lambda st, o=None, **kw: {"id": "j", "kind": "reel", "status": st, "input": {"crm_item": 11, **kw}, "output": o or {}}
r_post = crm.report_of(J("done", {"ig": {"state": "posted", "permalink": "https://www.instagram.com/reel/x/", "posted_at": "2026-11-24T08:01:00Z"},
                                  "ig_stats": {"views": 1200, "shares": 30, "saved": 12, "reach": 900}}))
ok(crm.report_of(J("running"))["status"] == "producing" and crm.report_of(J("review"))["status"] == "ready"
   and crm.report_of(J("review", {"hold": True}))["status"] == "held" and crm.report_of(J("failed"))["status"] == "failed"
   and crm.report_of(J("cancelled", {"decision": "auto_revise"})) is None and crm.report_of(J("cancelled"))["status"] == "failed"
   and r_post["status"] == "posted" and r_post["media"]["permalink"].startswith("https://") and r_post["stats"] == {"views": 1200, "reach": 900, "shares": 30, "saves": 12}
   and crm.report_of(J("review", {"autopilot_hold": True}, ad=True))["media"]["video"].startswith("https://studio."),
   "CR05 statuses map to the CRM: made, in review, held, posted with link and results, failed; an ad video points to Reel Studio")

t0 = datetime.now(timezone.utc)
db2 = DB([{"id": "a", "kind": "reel", "status": "cancelled", "input": {"crm_item": 11}, "output": {"decision": "auto_revise"}, "created_at": t0.isoformat()},
          {"id": "b", "kind": "reel", "status": "review", "input": {"crm_item": 11}, "output": {}, "created_at": (t0 + timedelta(minutes=5)).isoformat()}])
fake = CRM(); crm.call = fake
crm.sync(db2); first = list(fake.sent); crm.sync(db2)
ok(len(first) == 1 and first[0][1]["status"] == "ready" and len(fake.sent) == 1, "CR06 the newest job of an item is reported once; unchanged states are not re-sent")

fake = CRM(); crm.call = fake; crm._SIGNALS.update(at=0.0, data=None)
txt = crm.signals_text()
ok(crm.planned_reel_on(wk) and not crm.planned_reel_on(wk + timedelta(days=1)) and "wanted: CPL cost" in txt and "already planned" in txt
   and "brought enquiries" in txt, "CR07 the CRM's brief reaches the Strategist; Autopilot steps aside on days the plan has a reel")

ok("utm_campaign=content_11" in dm.message({"offer": "the fee plan", "points": ["a"], "link": "https://weoneaviation.in/fees", "keyword": "FEES", "campaign": "content_11"},
                                           {"name": "We One Aviation", "business": "", "niche": ""}), "CR08 a planned reel's DM link carries the CRM item's campaign")

# ---------------------------------------------------------------- the Carousel Designer's contract
good = {"title": "CPL cost, line by line", "caption": "c", "hashtags": ["#a"] * 5, "claims": [{"claim": "200 hours", "source_url": "https://dgca.gov.in"}],
        "slides": [{"kind": "cover", "headline": "Where does ₹45 lakh go?", "body": "Swipe for each part"}]
        + [{"kind": "point", "headline": f"Part {i}", "body": "Ground classes and exams"} for i in range(1, 5)] + [{"kind": "cta", "headline": "Save this", "body": "Send it to a parent"}]}
bad = {**good, "slides": good["slides"][:3] + [{"kind": "point", "headline": "x" * 70, "body": "visit weoneaviation.in or call 9876543210 — 100% placement"}]}
pb = carousel.check(bad, ["100% placement"])
ok(carousel.check(good, ["100% placement"]) == [] and any("5 to 10" in p for p in pb) and any("headline over 60" in p for p in pb)
   and any("no links" in p for p in pb) and any("banned" in p for p in pb) and any("last slide" in p for p in pb),
   "CR09 slides: 5-10, cover first, save/send last, short text, no links/phones, no banned claims")
brief_txt = json.dumps({"headline": "CPL in 18 months", "sub": "DGCA approved"})
ok(content.check({"headline": "CPL in 18 months", "sub": "DGCA approved", "badge": "", "cta": "Get the plan"}, brief_txt) == []
   and any("50%" in p for p in content.check({"headline": "50% off CPL", "sub": "", "badge": "", "cta": "x"}, brief_txt))
   and any("over 40" in p for p in content.check({"headline": "y" * 41, "sub": "", "badge": "", "cta": "x"}, brief_txt)),
   "CR10 ad picture words: within limits, and no number or promise that is not in the approved brief")

# ---------------------------------------------------------------- drawing
out = Path(ROOT) / "draw"
files = slides.carousel(good["slides"], out, config.FONTS_DIR)
f, lines, size = slides.fit("A very long headline " * 12, "black", 150, 900, 4, 78)
ims = [Image.open(p) for p in files]
pics = [slides.ad_picture({"headline": "CPL in 18 months", "sub": "DGCA approved course in Delhi NCR", "badge": "Admissions open", "cta": "Get the plan"}, s, out / f"ad_{s}.jpg", config.FONTS_DIR) for s in ("1080x1350", "1080x1920", "1080x1080")]
ok(len(lines) <= 4 and lines[-1].endswith("…") and len(files) == 6 and all(i.size == (1080, 1350) and i.format == "JPEG" for i in ims)
   and [Image.open(p).size for p in pics] == [(1080, 1350), (1080, 1920), (1080, 1080)],
   "CR11 slides are 1080x1350 JPEGs, ad pictures come in each size, and long text is shrunk and cut so it always fits")
# text is really drawn: the cover is not a blank gradient
px = ims[0].convert("L").resize((54, 67)); bright = sum(1 for v in px.getdata() if v > 200)
ok(bright > 40, "CR12 the cover shows its headline (bright text pixels present)")

# ---------------------------------------------------------------- 3D library
lib = Path(ROOT) / "3d"; lib.mkdir(parents=True)
for name in ("cessna-172-takeoff-runway.mp4", "cockpit-night-panel.mp4"):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=10:duration=3", "-pix_fmt", "yuv420p", str(lib / name)], check=True)
(lib / "cockpit-night-panel.txt").write_text("glass cockpit instruments A320")
hits = library3d.search("Cessna takeoff from the runway"); hits2 = library3d.search("A320 instruments")
cands = footage.search("cessna takeoff runway")
fr = library3d.frame("cockpit at night", Path(ROOT) / "fr.jpg")
ok(len(library3d.index()) == 2 and hits and hits[0]["dl"].endswith("cessna-172-takeoff-runway.mp4") and hits[0]["src"] == "ours"
   and hits2 and "cockpit" in hits2[0]["dl"] and cands and cands[0]["src"] == "ours" and fr and fr.exists()
   and library3d.search("helicopter rescue") == [], "CR13 our 3D clips are found by their names and notes, offered before stock footage, and give ad backgrounds")

# ---------------------------------------------------------------- whole jobs with scripted agents
def ctx_for(db, kind, inp):
    j = {"id": str(uuid.uuid4()), "kind": kind, "status": "running", "brand": "weone", "input": inp, "output": {}, "lease_owner": "mac", "attempts": 1}
    return JobContext(db, j, "mac")

asked = []
def fake_call(ctx, agent, text, schema, tools, timeout=0, check=None, **kw):
    asked.append((agent, text, tools))
    o = good if agent == "carousel_designer" else {"headline": "CPL in 18 months", "sub": "DGCA approved", "badge": "Delhi NCR", "cta": "Get the plan", "background": "cockpit night"}
    assert not (check and check(o)), check(o)
    return o
agents.call = fake_call; carousel.agents.call = fake_call; content.agents.call = fake_call
db3 = DB(); cx = ctx_for(db3, "reel", {**inp2, "crm_item": 12})
carousel.run(cx)
fin = [v for t, m, v in db3.ups if v.get("status") == "review"]
o = fin[-1]["output"] if fin else {}
ok(fin and o["format"] == "carousel" and len(o["slides"]) == 6 and all(p in db3.files for p in o["slides"]) and f"{cx.id}/thumb.jpg" in db3.files
   and o["ig_caption"].count("#") <= 5 and asked[0][0] == "carousel_designer" and "WebSearch" in asked[0][2] and "100% placement" in asked[0][1],
   "CR14 a carousel job: verified slides drawn, stored, captioned and sent to review like a reel")

fake = CRM(); crm.call = fake; content.crm.call = fake
db4 = DB(); cx = ctx_for(db4, "content", inp4)
content.run(cx)
pics_sent = [b for a, b in fake.sent if a == "picture"]
ok([p["size"] for p in pics_sent] == ["1080x1350", "1080x1920"] and all(p["id"] == 11 and len(p["image_b64"]) > 1000 for p in pics_sent)
   and any(v.get("status") == "done" for _, _, v in db4.ups), "CR15 an ad picture job draws each size and sends it to the CRM")
fake.sent.clear(); cx2 = JobContext(db4, {**cx.job, "status": "running"}, "mac"); content.run(cx2)
ok(not [a for a, _ in fake.sent if a == "picture"], "CR16 a retried picture job does not send sizes the CRM already has")

print(f"\n{sum(1 for r in R if r[0])}/{len(R)} passed")
sys.exit(0 if all(r[0] for r in R) else 1)

"""Minimal stand-in for Supabase (auth, PostgREST subset, storage) + static file server, for UI smoke tests only.

    python mock_supabase.py <web_dir> <fixtures_dir> <sample.mp4> [port]
"""
import json, os, re, sys, threading, time, uuid
import urllib.request, urllib.error
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

WEB, FIX, MP4 = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
PORT = int(sys.argv[4]) if len(sys.argv) > 4 else 8765
NOW = lambda: time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".000Z"
DB = {"jobs": {}, "job_events": [], "pings": {}, "workers": {"mac-mini": {"id": "mac-mini", "last_seen": NOW(), "status": "idle", "version": "1.0.0",
                                                                              "claude_ok": True, "claude_note": "2.1.300", "claude_checked_at": NOW(), "info": {"reels_today": 0, "daily_cap": 3}}},
      "brands": {b["id"]: b for b in [
          {"id": "weone", "name": "We One Aviation", "tagline": "DGCA ground classes", "website": "https://weoneaviation.in", "logo": "brands/weone.png", "color": "#0A2342", "instagram": "we_one_aviation", "status": "live", "sort": 1, "logo_asset": None, "logo_updated_at": None},
          {"id": "bookmycharter", "name": "Book My Charter", "tagline": "Private jet & helicopter charter", "website": "https://bookmycharter.in", "logo": "brands/bookmycharter.png", "color": "#13213D", "instagram": None, "status": "live", "sort": 2, "logo_asset": None, "logo_updated_at": None}]},
      "settings": {},
      "agents": {a[0]: {"id": a[0], "name": a[1], "role": a[2], "team": a[3], "sort": i} for i, a in enumerate([
          ("account_intel", "Account Intelligence", "Studies each account.", "intelligence"), ("strategist", "Strategist", "Picks topics.", "strategy"),
          ("fact_checker", "Fact-Checker", "Verifies facts.", "production"), ("scriptwriter", "Scriptwriter", "Writes scripts.", "production"),
          ("hook_critic", "Hook Critic", "Judges scripts.", "quality"), ("analyst", "Performance Analyst", "Learns from results.", "learning"),
          ("resource_manager", "Resource Manager", "Grades agents.", "learning"), ("supervisor", "Supervisor", "Checks contracts.", "control")])},
      "agent_runs": [{"agent": "scriptwriter", "ok": True, "grade": 82, "ms": 60000, "brand": "weone", "created_at": NOW()},
                     {"agent": "scriptwriter", "ok": False, "grade": None, "ms": 1000, "brand": "weone", "created_at": NOW()},
                     {"agent": "hook_critic", "ok": True, "grade": 78, "ms": 9000, "brand": "bookmycharter", "created_at": NOW()}],
      "agent_lessons": [{"id": 1, "agent": "scriptwriter", "brand": "weone", "lesson": "Open core reels with the exam rule as a number.", "why": "3 reels got 2x saves",
                         "metric": "engagement", "baseline": 2.0, "status": "active", "result": None, "created_by": "analyst", "created_at": NOW()},
                        {"id": 2, "agent": "scriptwriter", "brand": "bookmycharter", "lesson": "BMC ONLY lesson must not show on We One.", "why": "x",
                         "metric": "engagement", "baseline": 1.0, "status": "active", "result": None, "created_by": "analyst", "created_at": NOW()},
                        {"id": 3, "agent": "scriptwriter", "brand": None, "lesson": "Keep every line under 9 words.", "why": "critics flagged long lines",
                         "metric": "critic", "baseline": 70, "status": "rolled_back", "result": {"baseline": 70, "after": 58, "n": 4}, "created_by": "resource_manager", "created_at": NOW()}]}
USER = {"id": "11111111-1111-1111-1111-111111111111", "email": os.environ.get("USER_EMAIL", "owner@example.com"), "aud": "authenticated", "role": "authenticated",
        "app_metadata": {"provider": "email"}, "user_metadata": {}, "created_at": NOW()}
LOG = []
PGRST = os.environ.get("PGRST_URL")          # proxy mode: real PostgREST behind /rest/v1, real worker does the jobs
USER_JWT = os.environ.get("USER_JWT", "tok")
STORE = Path(os.environ.get("STORE_DIR", "/tmp/mock_storage"))


def event(jid, msg, level="info"):
    DB["job_events"].insert(0, {"id": len(DB["job_events"]) + 1, "job_id": jid, "ts": NOW(), "level": level, "message": msg})


def simulate(j):
    fx = lambda n: json.load(open(FIX / f"{n}.json"))
    time.sleep(1.5); j.update(status="running", stage="research", updated_at=NOW()); event(j["id"], "working")
    if j["kind"] == "suggest":
        time.sleep(2); j.update(status="done", output={"topics": fx("suggest")["topics"]}, progress=1, updated_at=NOW())
    elif j["kind"] == "optimize":
        time.sleep(2); j.update(status="done", output={"concept": fx("optimize")}, progress=1, updated_at=NOW())
    else:
        STEP = float(os.environ.get("MOCK_STEP", "1.2"))
        sp = fx("script"); out = {"title": sp["title"], "trace": {}, "timings": {}, "stage_started": {}}
        plan = {"script": [("research", "Research sources", "4 sources"), ("write", "Write narration", "6 lines"), ("facts", "Fact list", "2 claims")],
                "voice": [("G1", "Part 1", "clarity 0.93"), ("G2", "Part 2", "clarity 0.91"), ("G3", "Part 3", "clarity 0.95"), ("score", "Pick clearest takes", "1 word to check")],
                "footage": [("S1", "hook: bolt in dark sky", "pexels"), ("S2", "calm jet", "pixabay"), ("S3", "wing in clouds", "pexels"), ("pick", "Claude chooses clips", "")],
                "render": [("frames", "Draw frames", "1212 frames"), ("mix", "Mix sound", ""), ("master", "Master & encode", "-14.0 LUFS"), ("sheet", "Contact sheet", "")],
                "qc": [("auto", "Loudness, size, length", ""), ("watch", "Claude watches the reel", "score 8/10")],
                "publish": [("video", "Upload reel", "12.3 MB"), ("archive", "Save master on Mac", "")]}
        logs = {"script": ["Searching the web: lightning strike airliner frequency", "Reading skybrary.aero", "Reading faa.gov"],
                "footage": ["Looking at clip options for S1", "Looking at clip options for S3"], "qc": ["Watching the finished reel frame by frame"]}
        for st, p in [("script", .1), ("voice", .4), ("footage", .55), ("render", .8), ("qc", .93), ("publish", .98)]:
            if j["status"] == "cancelled": return
            out["stage_started"][st] = int(time.time()); out["trace"][st] = {n: {"label": l, "status": "wait", "info": ""} for n, l, _ in plan[st]}
            j.update(stage=st, progress=p, updated_at=NOW(), output=json.loads(json.dumps(out)))
            for k, (n, l, info) in enumerate(plan[st]):
                out["trace"][st][n] = {"label": l, "status": "run", "info": "working"}; j.update(output=json.loads(json.dumps(out)), updated_at=NOW())
                if logs.get(st) and k < len(logs[st]): event(j["id"], logs[st][k])
                time.sleep(STEP)
                out["trace"][st][n] = {"label": l, "status": "done", "info": info}
            out["timings"][st] = int(time.time()) - out["stage_started"][st]
            j.update(output=json.loads(json.dumps(out)), updated_at=NOW())
        caps = [("Udte *plane* par bijli girti hai…", "G1"), ("aur phir bhi *kuch nahi* hota", "G1"), ("Har plane par *saal mein ~1 baar*", "G2"),
                ("Bijli *metal skin* par behti hai", "G2"), ("Aisi hi *aviation stories* ke liye", "G3"), ("follow karein *We One Aviation*", "G3")]
        script = [{"t": round(0.1 + i * 3.3, 2), "end": round(3.2 + i * 3.3, 2), "text": t, "group": g} for i, (t, g) in enumerate(caps)]
        claims = [{**c, "group": "G2"} for c in sp["claims"]]
        j.update(status="review", progress=1, updated_at=NOW(), output={**out, "claims": claims, "video": f"{j['id']}/reel.mp4", "script": script,
                 "thumb": f"{j['id']}/thumb.jpg", "duration": 21.4, "lufs": -14.0, "true_peak": -2.1, "delivery_mb": 12.3,
                 "qc": fx("qc"), "weak_words": ["aviation"], "credits": [{"shot": "S1", "source": "pexels", "author": "A", "page": "https://www.pexels.com", "licence": "Pexels License"}],
                 "archive_on_mac": "~/ReelStudio/deliverables/2026-10-17/x_master.mp4"})

def filt(rows, q):
    for k, v in q.items():
        if k in ("select", "order", "limit", "offset"): continue
        v = v[0]
        if k == "or":
            alts = v.strip("()").split(",")
            def m_(r, a):
                f, op, val = a.split(".", 2)
                return (r.get(f) is None) if op == "is" else str(r.get(f)) == val
            rows = [r for r in rows if any(m_(r, a) for a in alts)]; continue
        if v.startswith("eq."): rows = [r for r in rows if str(r.get(k)) == v[3:]]
        elif v.startswith("in.("): vals = v[4:-1].split(","); rows = [r for r in rows if str(r.get(k)) in vals]
    if "order" in q:
        col, _, d = q["order"][0].partition("."); rows = sorted(rows, key=lambda r: str(r.get(col)), reverse=d.startswith("desc"))
    if "limit" in q: rows = rows[: int(q["limit"][0])]
    return rows


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k): super().__init__(*a, directory=str(WEB), **k)
    def log_message(self, *a): pass
    def cors(self):
        self.send_header("Access-Control-Allow-Origin", "*"); self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,DELETE,OPTIONS"); self.send_header("Access-Control-Expose-Headers", "*")
    def do_OPTIONS(self): self.send_response(204); self.cors(); self.end_headers()
    def js(self, obj, code=200):
        single = "vnd.pgrst.object" in self.headers.get("Accept", "")
        if single and isinstance(obj, list):
            if not obj: return self.js({"message": "no rows", "code": "PGRST116"}, 406)
            obj = obj[0]
        b = json.dumps(obj).encode(); self.send_response(code); self.cors()
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def body(self):
        n = int(self.headers.get("Content-Length") or 0); return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def proxy(self, method):
        n = int(self.headers.get("Content-Length") or 0); data = self.rfile.read(n) if n else None
        hdr = {k: v for k, v in self.headers.items() if k.lower() in ("authorization", "content-type", "accept", "prefer", "range")}
        req = urllib.request.Request(PGRST + self.path[len("/rest/v1"):], data=data, method=method, headers=hdr)
        try:
            r = urllib.request.urlopen(req); code, body, ct = r.status, r.read(), r.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            code, body, ct = e.code, e.read(), e.headers.get("Content-Type", "application/json")
        self.send_response(code); self.cors(); self.send_header("Content-Type", ct); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_PATCH(self):
        if PGRST and self.path.startswith("/rest/v1/"): return self.proxy("PATCH")
        self.js({"message": "not mocked"}, 404)

    def do_DELETE(self):
        if PGRST and self.path.startswith("/rest/v1/"): return self.proxy("DELETE")
        self.js({}, 200)

    def do_GET(self):
        if PGRST and self.path.startswith("/rest/v1/"): return self.proxy("GET")
        u = urlparse(self.path); q = parse_qs(u.query); p = u.path; LOG.append(("GET", p))
        if p == "/auth/v1/user": return self.js(USER)
        m = re.match(r"/rest/v1/(\w+)$", p)
        if m:
            t = m.group(1); rows = list(DB[t].values()) if isinstance(DB[t], dict) else list(DB[t])
            if t == "pings":
                for r in rows:
                    if not r["answered_at"] and time.time() - r["_t"] > 2:
                        r.update(answered_at=NOW(), worker_id="mac-mini", reply={"claude_ok": True, "claude_checking": False, "reels_today": 0, "daily_cap": 3, "version": "1.0.0"})
            if t == "workers": DB["workers"]["mac-mini"]["last_seen"] = NOW()
            return self.js(filt(rows, q))
        if p.startswith("/storage/v1/object/reels/"):                 # authenticated download (worker / poster), byte ranges supported
            real = STORE / p[len("/storage/v1/object/reels/"):]
            data = real.read_bytes() if real.exists() else b""
            rng = re.match(r"bytes=(\d+)-(\d+)", self.headers.get("Range") or "")
            if data and rng:
                a, b = int(rng.group(1)), min(int(rng.group(2)), len(data) - 1); part = data[a:b + 1]
                self.send_response(206); self.cors(); self.send_header("Content-Range", f"bytes {a}-{b}/{len(data)}")
                self.send_header("Content-Length", str(len(part))); self.end_headers(); return self.wfile.write(part)
            self.send_response(200 if data else 404); self.cors(); self.send_header("Content-Length", str(len(data))); self.end_headers(); return self.wfile.write(data)
        if p.startswith("/storage/v1/object/sign/brand-assets/"):
            real = STORE / "brand-assets" / p[len("/storage/v1/object/sign/brand-assets/"):]
            data = real.read_bytes() if real.exists() else b""
            self.send_response(200 if data else 404); self.cors(); self.send_header("Content-Type", "image/png"); self.send_header("Content-Length", str(len(data))); self.end_headers(); return self.wfile.write(data)
        if p.startswith("/storage/v1/object/sign/reels/"):
            real = STORE / p[len("/storage/v1/object/sign/reels/"):]
            f = real if real.exists() else MP4 if p.endswith(".mp4") else WEB / "tests" / "thumb.jpg"
            data = f.read_bytes() if f.exists() else b""
            self.send_response(200); self.cors(); self.send_header("Content-Type", "video/mp4" if p.endswith(".mp4") else "image/jpeg")
            self.send_header("Content-Length", str(len(data))); self.end_headers(); return self.wfile.write(data)
        return super().do_GET()

    def do_POST(self):
        if PGRST and self.path.startswith("/rest/v1/"): return self.proxy("POST")
        u = urlparse(self.path); p = u.path
        mb = re.match(r"/storage/v1/object/(reels|brand-assets)/(.+)$", p)
        if mb:                                                     # upload (worker, or a logo from the site)
            n = int(self.headers.get("Content-Length") or 0); dest = STORE / mb.group(1) / mb.group(2)
            data = self.rfile.read(n); ct = self.headers.get("Content-Type") or ""
            if ct.startswith("multipart/form-data"):                  # supabase-js sends files as a form: keep only the file part
                import email.parser, email.policy
                msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(b"Content-Type: " + ct.encode() + b"\r\n\r\n" + data)
                data = next((part.get_payload(decode=True) for part in msg.iter_parts() if part.get_filename() or part.get_content_type().startswith("image/")), data)
            dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(data); LOG.append(("UPLOAD", p, len(data))); return self.js({"Key": p})
        if p.startswith("/storage/v1/object/list/reels"):
            return self.js([])
        b = self.body(); LOG.append(("POST", p, b))
        if p == "/auth/v1/token":
            if b.get("password") != "secret": return self.js({"error": "invalid_grant", "error_description": "Invalid login credentials"}, 400)
            return self.js({"access_token": USER_JWT, "token_type": "bearer", "expires_in": 3600, "expires_at": int(time.time()) + 3600, "refresh_token": "r", "user": USER})
        if p == "/auth/v1/logout": self.send_response(204); self.cors(); self.end_headers(); return
        if p == "/rest/v1/pings":
            r = {"id": len(DB["pings"]) + 1, "created_at": NOW(), "created_by": USER["id"], "answered_at": None, "worker_id": None, "reply": None, "_t": time.time()}
            DB["pings"][r["id"]] = r; return self.js([r], 201)
        if p == "/rest/v1/rpc/create_job":
            j = {"id": str(uuid.uuid4()), "kind": b["p_kind"], "status": "queued", "stage": None, "progress": 0, "input": b.get("p_input") or {}, "output": {},
                 "error": None, "attempts": 0, "parent_id": b.get("p_parent"), "created_at": NOW(), "updated_at": NOW(), "not_before": None}
            DB["jobs"][j["id"]] = j; threading.Thread(target=simulate, args=(j,), daemon=True).start(); return self.js(j)
        if p == "/rest/v1/rpc/set_review_decision":
            j = DB["jobs"][b["p_job"]]; j["status"] = "done" if b["p_decision"] == "approve" else "cancelled"
            j["output"] = {**j["output"], "decision": b["p_decision"], "notes": b.get("p_notes")}; return self.js(j)
        if p == "/rest/v1/rpc/cancel_job":
            DB["jobs"][b["p_job"]]["status"] = "cancelled"; return self.js(None)
        if p.startswith("/storage/v1/object/sign/"):
            return self.js({"signedURL": p.replace("/storage/v1", "") + "?token=t"})
        if p == "/rest/v1/rpc/add_brand":
            bid = re.sub(r"[^a-z0-9]+", "-", b["p_name"].lower()).strip("-")[:20]
            DB["brands"][bid] = {"id": bid, "name": b["p_name"], "tagline": "", "website": b["p_website"], "logo": None, "color": "#335", "instagram": None,
                                 "status": "setup", "sort": 9, "logo_asset": None, "logo_updated_at": None, "intel_at": None}
            j = {"id": str(uuid.uuid4()), "kind": "onboard", "brand": bid, "status": "queued", "input": {"brand": bid}, "output": {}, "created_at": NOW(), "updated_at": NOW()}
            DB["jobs"][j["id"]] = j; LOG.append(("ADD", bid)); return self.js({"brand": bid, "job": j["id"]})
        if p == "/rest/v1/rpc/run_onboard":
            j = {"id": str(uuid.uuid4()), "kind": "onboard", "brand": b["p_brand"], "status": "queued", "input": {"brand": b["p_brand"]}, "output": {}, "created_at": NOW(), "updated_at": NOW()}
            DB["jobs"][j["id"]] = j; LOG.append(("STUDY", b["p_brand"])); return self.js(j["id"])
        if p == "/rest/v1/rpc/set_brand_logo":
            br = DB["brands"][b["p_brand"]]
            if not str(b["p_path"]).startswith(b["p_brand"] + "/"): return self.js({"message": "That file does not belong to this account."}, 400)
            br.update(logo_asset=b["p_path"], logo_updated_at=NOW()); return self.js({"brand": b["p_brand"], "logo_asset": b["p_path"]})
        return self.js({"message": "not mocked " + p}, 404)


if __name__ == "__main__":
    (WEB / "config.js").write_text(f'export const SUPABASE_URL = "http://127.0.0.1:{PORT}";\nexport const SUPABASE_ANON_KEY = "anon";\n')
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()

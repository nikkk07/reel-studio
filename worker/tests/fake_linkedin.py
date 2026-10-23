"""Stand-in for LinkedIn (OAuth + REST: organizationAcls, organizations, videos, posts) for tests.
    python fake_linkedin.py <port> <state_dir>
Scenario switches (state_dir/scenario.json): {"valid_tokens": [...], "part_500_once": bool, "lose_post_response": bool, "processing_polls": n}
The video parts really are reassembled and compared with the stored file size; little-text commentary is checked for unescaped characters.
"""
import json, re, sys, threading, time, uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

PORT, STATE = int(sys.argv[1]), Path(sys.argv[2]); STATE.mkdir(parents=True, exist_ok=True)
DB = {"videos": {}, "posts": [], "codes": {}, "log": []}
LOCK = threading.Lock()
PART = 4194304


def scen():
    p = STATE / "scenario.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_scen(s): (STATE / "scenario.json").write_text(json.dumps(s))
def dump(): (STATE / "state.json").write_text(json.dumps({k: v for k, v in DB.items() if k != "codes"}, indent=1, default=str))


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send(self, obj, code=200, headers=None):
        b = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(code)
        for k, v in (headers or {}).items(): self.send_header(k, v)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0); return self.rfile.read(n) if n else b""

    def authed(self):
        tok = (self.headers.get("Authorization") or "").replace("Bearer ", "")
        if tok not in scen().get("valid_tokens", []):
            self.send({"status": 401, "serviceErrorCode": 65601, "message": "The token used in the request has expired"}, 401); return False
        if self.headers.get("X-Restli-Protocol-Version") != "2.0.0" or not re.fullmatch(r"\d{6}", self.headers.get("LinkedIn-Version", "")):
            self.send({"status": 400, "message": "missing LinkedIn-Version / X-Restli-Protocol-Version"}, 400); return False
        return True

    def do_POST(self):
        u = urlparse(self.path); p = u.path; q = parse_qs(u.query); raw = self.body()
        DB["log"].append(["POST", self.path[:80]])
        if p == "/oauth/v2/accessToken":
            f = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
            if f.get("client_secret") != "secretABC123xyz" or f.get("code") != "goodcode" or not f.get("redirect_uri", "").startswith("http"):
                return self.send({"error": "invalid_request", "error_description": "Unable to retrieve access token: appid/redirect uri/code verifier does not match"}, 400)
            tok = "AQV" + uuid.uuid4().hex * 3; s = scen(); s.setdefault("valid_tokens", []).append(tok); save_scen(s)
            return self.send({"access_token": tok, "expires_in": 5184000, "scope": "r_organization_social,rw_organization_admin,w_organization_social"})
        if not self.authed(): return
        b = json.loads(raw or b"{}")
        with LOCK:
            if p == "/rest/videos" and q.get("action") == ["initializeUpload"]:
                r = b["initializeUploadRequest"]; size = int(r["fileSizeBytes"]); vid = f"urn:li:video:D{uuid.uuid4().hex[:12]}"
                parts = [{"uploadUrl": f"http://127.0.0.1:{PORT}/upload/{vid}/{i}", "firstByte": a, "lastByte": min(size, a + PART) - 1} for i, a in enumerate(range(0, size, PART))]
                DB["videos"][vid] = {"owner": r["owner"], "size": size, "parts": {}, "status": "WAITING_UPLOAD", "polls": 0, "token": "tok" + vid[-4:]}
                dump(); return self.send({"value": {"video": vid, "uploadInstructions": parts, "uploadToken": "tok" + vid[-4:], "uploadUrlsExpireAt": int(time.time() * 1000) + 86400000}})
            if p == "/rest/videos" and q.get("action") == ["finalizeUpload"]:
                r = b["finalizeUploadRequest"]; v = DB["videos"].get(r["video"])
                if not v or v["status"] != "WAITING_UPLOAD": return self.send({"status": 400, "message": "video already finalized or unknown"}, 400)
                got = [v["parts"][i][1] for i in sorted(v["parts"])]
                if r["uploadedPartIds"] != got or sum(v["parts"][i][0] for i in v["parts"]) != v["size"] or r["uploadToken"] != v["token"]:
                    return self.send({"status": 400, "message": "parts do not match"}, 400)
                v["status"] = "PROCESSING"; dump(); return self.send(None, 200)
            if p == "/rest/posts":
                c = b.get("commentary", "")
                bad = re.findall(r"(?<!\\)[|{}@\[\]()<>*_~]", c)
                if bad: return self.send({"status": 422, "message": f"commentary has unescaped reserved characters {bad[:5]}"}, 422)
                v = DB["videos"].get(b["content"]["media"]["id"])
                if not v or v["status"] != "AVAILABLE": return self.send({"status": 400, "message": "video not available"}, 400)
                if b["author"] != v["owner"] or b["visibility"] != "PUBLIC" or b["lifecycleState"] != "PUBLISHED": return self.send({"status": 400, "message": "bad post"}, 400)
                urn = f"urn:li:share:7{len(DB['posts']):018d}"
                DB["posts"].append({"id": urn, "author": b["author"], "commentary": c, "content": b["content"], "ts": time.time()})
                s = scen(); dump()
                if s.pop("lose_post_response", False): save_scen(s); self.connection.close(); return
                return self.send(None, 201, {"x-restli-id": urn})
        self.send({"status": 404, "message": "not found"}, 404)

    def do_PUT(self):
        m = re.match(r"/upload/(urn:li:video:\w+)/(\d+)", unquote(self.path)); raw = self.body()
        if not m: return self.send({"message": "bad upload"}, 404)
        s = scen()
        if s.pop("part_500_once", False) and m.group(2) == "1":
            save_scen(s); return self.send({"message": "upstream error"}, 500)
        v = DB["videos"][m.group(1)]; etag = uuid.uuid4().hex
        v["parts"][int(m.group(2))] = (len(raw), etag); dump()
        self.send(None, 200, {"ETag": etag})

    def do_GET(self):
        u = urlparse(self.path); p = unquote(u.path); q = parse_qs(u.query)
        if not self.authed(): return
        DB["log"].append(["GET", self.path[:80]])
        if p == "/rest/organizationAcls":
            return self.send({"elements": [{"organization": "urn:li:organization:111", "role": "ADMINISTRATOR", "state": "APPROVED"},
                                           {"organization": "urn:li:organization:222", "role": "ADMINISTRATOR", "state": "APPROVED"}]})
        m = re.match(r"/rest/organizations/(\d+)$", p)
        if m:
            return self.send({"111": {"localizedName": "Some Other Page", "vanityName": "other-page"}, "222": {"localizedName": "We One Aviation", "vanityName": "we-one-aviation"}}[m.group(1)])
        m = re.match(r"/rest/videos/(urn:li:video:\w+)$", p)
        if m:
            v = DB["videos"][m.group(1)]
            if v["status"] == "PROCESSING":
                v["polls"] += 1
                if v["polls"] >= scen().get("processing_polls", 2): v["status"] = "AVAILABLE"
            dump(); return self.send({"id": m.group(1), "status": v["status"]})
        if p == "/rest/posts" and q.get("q") == ["author"]:
            return self.send({"elements": [x for x in DB["posts"][::-1] if x["author"] == q["author"][0]][:10]})
        self.send({"status": 404, "message": "not found"}, 404)


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()

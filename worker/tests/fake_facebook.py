"""Stand-in for Facebook Graph (token exchange, /me/accounts, Page video_reels start/finish, rupload with file_url, status).
    python fake_facebook.py <port> <state_dir>       scenario.json: {"user_tokens": [...], "lose_upload_response": bool, "publish_polls": n}
The file_url is really downloaded and checked; a Reel can only be finished once, after its upload.
"""
import json, re, sys, threading, time, urllib.request, uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

PORT, STATE = int(sys.argv[1]), Path(sys.argv[2]); STATE.mkdir(parents=True, exist_ok=True)
SECRET = "0123456789abcdef0123456789abcdef"
DB = {"long": set(), "pages": {"111": ("Some Other Page", "PAGE_OTHER" + "x" * 60), "222": ("We One Aviation", "PAGE_WOA" + "y" * 60)}, "videos": {}, "published": []}
LOCK = threading.Lock()


def scen():
    p = STATE / "scenario.json"; return json.loads(p.read_text()) if p.exists() else {}
def save(s): (STATE / "scenario.json").write_text(json.dumps(s))
def dump(): (STATE / "state.json").write_text(json.dumps({"videos": DB["videos"], "published": DB["published"]}, indent=1, default=str))


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, obj, code=200):
        b = json.dumps(obj).encode(); self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def err(self, msg, code=100, http=400): self.send({"error": {"message": msg, "type": "OAuthException", "code": code}}, http)
    def page_tok(self, tok): return next((pid for pid, (_, t) in DB["pages"].items() if t == tok and tok not in scen().get("revoked", [])), None)

    def do_GET(self):
        u = urlparse(self.path); q = {k: v[0] for k, v in parse_qs(u.query).items()}; p = u.path
        if p.endswith("/oauth/access_token"):
            if q.get("client_secret") != SECRET: return self.err("Error validating client secret.", 1)
            if q.get("fb_exchange_token") not in scen().get("user_tokens", []): return self.err("Error validating access token: Session has expired", 190)
            t = "EAALONG" + uuid.uuid4().hex * 2; DB["long"].add(t); return self.send({"access_token": t, "token_type": "bearer"})
        if p.endswith("/me/accounts"):
            if q.get("access_token") not in DB["long"]: return self.err("Invalid OAuth access token", 190)
            if scen().get("accounts_empty"): return self.send({"data": []})
            return self.send({"data": [{"id": pid, "name": n, "access_token": t, "tasks": ["ADVERTISE", "ANALYZE", "CREATE_CONTENT", "MODERATE", "MANAGE"]} for pid, (n, t) in DB["pages"].items()]})
        if p.endswith("/debug_token"):
            if q.get("access_token", "").split("|")[-1] != SECRET: return self.err("Invalid app token", 190)
            sc = scen().get("scopes", ["pages_show_list", "pages_read_engagement", "pages_manage_posts"])
            return self.send({"data": {"is_valid": q.get("input_token") in DB["long"], "scopes": sc,
                                       "granular_scopes": [{"scope": s, "target_ids": scen().get("granted_pages", ["222"])} for s in sc if s.startswith("pages_")]}})
        if p.endswith("/me") and q.get("access_token") in DB["long"]: return self.send({"id": "9", "name": "Nikhil Bisht"})
        m0 = re.match(r"/v\d+\.\d+/(\d+)$", p)
        if m0 and q.get("access_token") in DB["long"] and m0.group(1) in DB["pages"]:
            n, t = DB["pages"][m0.group(1)]; return self.send({"id": m0.group(1), "name": n, "access_token": t, "tasks": ["CREATE_CONTENT", "MANAGE"]})
        pid = self.page_tok(q.get("access_token"))
        if not pid: return self.err("Error validating access token: The session has been invalidated", 190)
        m = re.match(r"/v\d+\.\d+/(\d+)$", p)
        if m and m.group(1) in DB["pages"]: return self.send({"id": m.group(1), "name": DB["pages"][m.group(1)][0]})
        if m and m.group(1) in DB["videos"]:
            v = DB["videos"][m.group(1)]
            if v["finished"] and v["publish"] != "published":
                v["polls"] += 1
                if v["polls"] >= scen().get("publish_polls", 2): v["publish"] = "published"; DB["published"].append(m.group(1))
            dump()
            return self.send({"status": {"video_status": "ready" if v["uploaded"] else "upload_complete" if v["uploaded"] else "uploading",
                                         "uploading_phase": {"status": "complete" if v["uploaded"] else "not_started"},
                                         "publishing_phase": {"status": "complete" if v["publish"] == "published" else "in_progress", "publish_status": v["publish"]}},
                              "permalink_url": f"/reel/{m.group(1)}/" if v["publish"] == "published" else None})
        self.err("Unsupported get request")

    def do_POST(self):
        u = urlparse(self.path); n = int(self.headers.get("Content-Length") or 0)
        q = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode() if n else "").items()}
        m = re.match(r"/video-upload/v\d+\.\d+/(\d+)$", u.path)
        with LOCK:
            if m:
                tok = (self.headers.get("Authorization") or "").replace("OAuth ", "")
                if not self.page_tok(tok): return self.err("Invalid OAuth access token", 190)
                v = DB["videos"].get(m.group(1)); url = self.headers.get("file_url")
                if not v or not url: return self.err("missing video or file_url")
                data = urllib.request.urlopen(url).read()
                if len(data) < 10000 or data[4:8] != b"ftyp": return self.err("The video file is not a valid mp4", 352)
                v["uploaded"] = True; v["bytes"] = len(data); dump()
                s = scen()
                if s.pop("lose_upload_response", False): save(s); self.connection.close(); return
                return self.send({"success": True})
            pid = self.page_tok(q.get("access_token"))
            if not pid: return self.err("Error validating access token: The session has been invalidated", 190)
            m = re.match(r"/v\d+\.\d+/(\d+)/video_reels$", u.path)
            if m and m.group(1) == pid:
                if q.get("upload_phase") == "start":
                    vid = str(9000000 + len(DB["videos"])); DB["videos"][vid] = {"page": pid, "uploaded": False, "finished": False, "publish": "draft", "polls": 0}
                    dump(); return self.send({"video_id": vid, "upload_url": f"http://127.0.0.1:{PORT}/video-upload/v26.0/{vid}"})
                if q.get("upload_phase") == "finish":
                    v = DB["videos"].get(q.get("video_id"))
                    if not v or not v["uploaded"]: return self.err("Video not uploaded")
                    if v["finished"]: return self.err("Reel already published", 100)
                    if q.get("video_state") != "PUBLISHED": return self.err("bad state")
                    v["finished"] = True; v["description"] = q.get("description", ""); v["title"] = q.get("title"); dump()
                    return self.send({"success": True})
        self.err("Unsupported post request")


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()

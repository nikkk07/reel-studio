"""Stand-in for graph.instagram.com (Instagram API with Instagram Login) used by tests. It really downloads the video
from the signed URL and checks it against Instagram's Reels spec, so a pass here means the file and URL are right.

    python fake_instagram.py <port> <state_dir>
Scenario switches (state_dir/scenario.json): {"valid_tokens": [...], "container_error_once": bool, "publish_5xx_once": bool}
"""
import json, struct, subprocess, sys, tempfile, threading, time, urllib.request, uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

PORT, STATE = int(sys.argv[1]), Path(sys.argv[2]); STATE.mkdir(parents=True, exist_ok=True)
DB = {"containers": {}, "media": [], "log": []}
LOCK = threading.Lock()


def scen():
    p = STATE / "scenario.json"
    return json.loads(p.read_text()) if p.exists() else {}


def dump():
    (STATE / "state.json").write_text(json.dumps(DB, indent=1))


def atoms(path):
    out, data = [], open(path, "rb").read(); i = 0
    while i + 8 <= len(data):
        size, typ = struct.unpack(">I4s", data[i:i + 8]); out.append(typ.decode("latin1"))
        if size == 1: size = struct.unpack(">Q", data[i + 8:i + 16])[0]
        if size < 8: break
        i += size
    return out


def check_video(url):
    f = Path(tempfile.mkdtemp()) / "v.mp4"
    urllib.request.urlretrieve(url, f)
    a = atoms(f)
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration,size:stream=codec_type,codec_name,sample_rate,bit_rate,width,height,pix_fmt",
                                      "-of", "json", str(f)], capture_output=True, text=True).stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video"); au = next(s for s in info["streams"] if s["codec_type"] == "audio")
    problems = []
    if a.index("moov") > a.index("mdat"): problems.append("moov atom not at the front")
    if v["codec_name"] not in ("h264", "hevc") or v.get("pix_fmt") != "yuv420p": problems.append("video codec")
    if int(v["width"]) > 1920: problems.append("width")
    if au["codec_name"] != "aac" or int(au["sample_rate"]) > 48000: problems.append("audio codec/rate")
    if int(au.get("bit_rate") or 0) > 160000: problems.append(f"audio bitrate {au.get('bit_rate')}")
    d = float(info["format"]["duration"])
    if not 3 <= d <= 900: problems.append("duration")
    return problems, {"duration": d, "width": v["width"], "height": v["height"], "audio_bitrate": au.get("bit_rate")}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send(self, obj, code=200):
        b = json.dumps(obj).encode(); self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def err(self, msg, code=100, http=400, sub=None):
        self.send({"error": {"message": msg, "type": "OAuthException" if code == 190 else "IGApiException", "code": code, "error_subcode": sub}}, http)

    def auth(self, q):
        tok = (q.get("access_token") or [""])[0]
        if tok not in scen().get("valid_tokens", []):
            self.err("Error validating access token: Session has expired", 190); return None
        return tok

    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query); p = u.path
        tok = self.auth(q)
        if tok is None: return
        DB["log"].append(["GET", p])
        if p.endswith("/refresh_access_token"):
            new = "IGAArefreshed" + uuid.uuid4().hex * 2
            sc = scen(); sc["valid_tokens"].append(new); (STATE / "scenario.json").write_text(json.dumps(sc))
            return self.send({"access_token": new, "token_type": "bearer", "expires_in": 5183944})
        if p.endswith("/me"):
            return self.send({"user_id": "17841400000000001", "username": "weoneaviation", "account_type": "BUSINESS", "id": "26000000000000001"})
        if p.endswith("/media"):
            return self.send({"data": [{"id": m["id"], "caption": m["caption"], "permalink": m["permalink"], "timestamp": m["ts"]} for m in DB["media"][::-1]]})
        if p.endswith("/insights"):                       # media insights (Instagram Login): scenario insights = full | core_only | no_perm
            mode = scen().get("insights", "full"); metrics = (q.get("metric") or [""])[0].split(",")
            if mode == "no_perm": return self.err("(#10) Application does not have permission for this action", 10, 403)
            if mode == "core_only" and "reels_skip_rate" in metrics: return self.err("(#100) metric[8] must be one of the following values: ...", 100)
            vals = {"views": 1500, "reach": 1200, "likes": 60, "comments": 5, "shares": 14, "saved": 9, "total_interactions": 88, "ig_reels_avg_watch_time": 11234, "reels_skip_rate": 41.5}
            return self.send({"data": [{"name": m, "period": "lifetime", "values": [{"value": vals[m]}]} for m in metrics if m in vals]})
        if (q.get("fields") or [""])[0] == "like_count,comments_count":
            return self.send({"like_count": 61, "comments_count": 6, "id": p.rsplit("/", 1)[-1]})
        cid = p.rsplit("/", 1)[-1]
        if cid in DB["containers"]:
            c = DB["containers"][cid]
            if c["status"] == "IN_PROGRESS" and time.time() - c["t"] > 2:
                c["status"] = "ERROR" if c.get("fail") else "FINISHED"
            dump(); return self.send({"status_code": c["status"], "status": "ERROR: Video processing failed (2207026)" if c["status"] == "ERROR" else c["status"], "id": cid})
        m = next((m for m in DB["media"] if m["id"] == cid), None)
        if m: return self.send({"id": m["id"], "permalink": m["permalink"]})
        self.err("Unsupported get request", 100)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); q = parse_qs(self.rfile.read(n).decode())
        tok = self.auth(q)
        if tok is None: return
        p = urlparse(self.path).path; f = {k: v[0] for k, v in q.items()}
        DB["log"].append(["POST", p, {k: v for k, v in f.items() if k != "access_token"}])
        with LOCK:
            if p.endswith("/media"):
                if f.get("media_type") != "REELS": return self.err("media_type must be REELS")
                if len(f.get("caption", "")) > 2200 or f.get("caption", "").count("#") > 30: return self.err("caption too long")
                problems, info = check_video(f["video_url"])
                if problems: return self.err("The video file you selected is in a format that we don't support: " + ", ".join(problems), 352)
                if f.get("cover_url"): urllib.request.urlopen(f["cover_url"]).read()
                cid = str(17900000000000000 + len(DB["containers"]))
                sc = scen(); fail = sc.pop("container_error_once", False); (STATE / "scenario.json").write_text(json.dumps(sc))
                DB["containers"][cid] = {"status": "IN_PROGRESS", "t": time.time(), "caption": f.get("caption", ""), "fail": fail, "video": info, "share_to_feed": f.get("share_to_feed")}
                dump(); return self.send({"id": cid})
            if p.endswith("/media_publish"):
                sc = scen()
                if sc.pop("publish_5xx_once", False):
                    (STATE / "scenario.json").write_text(json.dumps(sc)); return self.err("An unexpected error has occurred. Please retry your request later.", 2, 500)
                c = DB["containers"].get(f.get("creation_id"))
                if not c or c["status"] != "FINISHED": return self.err("Media ID is not available", 9007, 400, 2207027)
                c["status"] = "PUBLISHED"; mid = str(18000000000000000 + len(DB["media"]))
                DB["media"].append({"id": mid, "caption": c["caption"], "permalink": f"https://www.instagram.com/reel/TEST{mid[-4:]}/", "ts": time.strftime("%Y-%m-%dT%H:%M:%S+0000", time.gmtime()), "container": f["creation_id"]})
                if sc.pop("crash_after_publish", False):          # simulate the Mac losing the response
                    (STATE / "scenario.json").write_text(json.dumps(sc)); dump(); self.connection.close(); return
                dump(); return self.send({"id": mid})
        self.err("Unsupported post request")


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()

"""HeyGen API: lip-sync an existing photo avatar to our own recorded voice (Avatar IV), 9:16. The API key comes from the
HEYGEN_API_KEY environment variable or a hidden prompt; it is never printed or saved.

    python3 heygen_avatar.py list                                   # your photo-avatar looks (id, name, size)
    python3 heygen_avatar.py make --look <id> --audio oncam.wav --out heygen.mp4 [--yes]

`make` shows the cost (Photo Avatar IV is $0.10 per second of output, HeyGen developer pricing) and the wallet balance,
and refuses when the wallet cannot cover it. It submits ONE job, polls it and downloads the video.
"""
import argparse, getpass, json, mimetypes, os, subprocess, sys, time, urllib.error, urllib.request, uuid

API = "https://api.heygen.com"
RATE_PER_S = 0.10
KEY = None


def key():
    global KEY
    if not KEY:
        KEY = os.environ.get("HEYGEN_API_KEY") or getpass.getpass("HeyGen API key (hidden): ").strip()
    return KEY


def call(method, path, body=None, headers=None, raw=None):
    h = {"x-api-key": key(), "accept": "application/json", **(headers or {})}
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    if body is not None and raw is None:
        h["content-type"] = "application/json"
    req = urllib.request.Request(API + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"HeyGen {method} {path} -> {e.code}: {e.read().decode()[:400]}")


def wallet():
    me = call("GET", "/v3/users/me").get("data") or {}
    return float(((me.get("wallet") or {}).get("remaining_balance")) or 0)


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout.strip()
    return float(out)


def upload(path):
    b = uuid.uuid4().hex
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{os.path.basename(path)}\"\r\n"
            f"Content-Type: {mime}\r\n\r\n").encode() + open(path, "rb").read() + f"\r\n--{b}--\r\n".encode()
    r = call("POST", "/v3/assets", raw=body, headers={"content-type": f"multipart/form-data; boundary={b}", "Idempotency-Key": uuid.uuid4().hex})
    return r["data"]["asset_id"]


def cmd_list(_):
    tok, n = None, 0
    while True:
        q = "/v3/avatars/looks?ownership=private&avatar_type=photo_avatar&limit=50" + (f"&token={tok}" if tok else "")
        r = call("GET", q)
        for lk in r.get("data") or []:
            n += 1
            print(f"{lk['id']}  | {lk.get('name')} | {lk.get('image_width')}x{lk.get('image_height')} | {lk.get('preferred_orientation')} | "
                  f"status {lk.get('status')} | engines {lk.get('supported_api_engines')}")
        if not r.get("has_more"):
            break
        tok = r.get("next_token")
    print(f"{n} photo-avatar look(s)")


def cmd_make(o):
    secs = duration(o.audio)
    cost = round(secs * RATE_PER_S, 2); bal = wallet()
    print(f"audio {secs:.1f} s -> about ${cost:.2f} (Photo Avatar IV, $0.10/s); wallet ${bal:.2f}")
    if cost > bal:
        raise SystemExit("not enough in the wallet for this length: shorten the audio or top up first. Nothing was sent.")
    if not o.yes and input("Start this ONE HeyGen job? [y/N] ").strip().lower() != "y":
        raise SystemExit("cancelled; nothing was sent")
    aid = upload(o.audio)
    body = {"type": "avatar", "avatar_id": o.look, "audio_asset_id": aid, "aspect_ratio": "9:16", "resolution": "1080p",
            "expressiveness": o.expressiveness, "title": o.title}
    if o.motion:
        body["motion_prompt"] = o.motion
    r = call("POST", "/v3/videos", body, headers={"Idempotency-Key": uuid.uuid4().hex})
    vid = r["data"]["video_id"]; print("video_id", vid, flush=True)
    while True:
        time.sleep(20)
        d = call("GET", f"/v3/videos/{vid}").get("data") or {}
        st = d.get("status"); print(time.strftime("%H:%M:%S"), st, flush=True)
        if st == "completed":
            urllib.request.urlretrieve(d["video_url"], o.out)
            print(f"saved {o.out}  ({d.get('duration')} s)  wallet now ${wallet():.2f}")
            return
        if st == "failed":
            raise SystemExit(f"failed: {d.get('failure_code')} {d.get('failure_message')}")


def main():
    a = argparse.ArgumentParser(); sub = a.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    m = sub.add_parser("make")
    m.add_argument("--look", required=True); m.add_argument("--audio", required=True); m.add_argument("--out", required=True)
    m.add_argument("--title", default="We One Aviation ad - presenter")
    m.add_argument("--expressiveness", default="medium", choices=["low", "medium", "high"])
    m.add_argument("--motion", default="Warm, confident counsellor speaking to camera with small natural hand gestures, steady posture.")
    m.add_argument("--yes", action="store_true")
    o = a.parse_args()
    {"list": cmd_list, "make": cmd_make}[o.cmd](o)


if __name__ == "__main__":
    sys.exit(main())

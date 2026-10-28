"""Where finished reels live while they wait to be posted.

* Cloudflare R2 when it is set up (and checked) in Studio Settings: 10 GB free, no download fees.
* Supabase Storage otherwise, so posting never stops while R2 is not set up.
Only the posting copy is uploaded (Instagram-spec MP4, also used by Facebook and LinkedIn) plus a small thumbnail,
and the cloud deletes the video a few days after it is posted (see the cloud poster's retention step).

R2 speaks the S3 API; requests are signed with AWS Signature V4 (stdlib only, no extra packages on the Mac).
Credentials are read from the cloud database and never logged.
"""
import datetime, hashlib, hmac, re, urllib.error, urllib.parse, urllib.request
from xml.etree import ElementTree

REGION, SERVICE = "auto", "s3"


def _h(key, msg):
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def _q(s, safe="-_.~"):
    return urllib.parse.quote(s, safe=safe)


class R2:
    def __init__(self, account_id, bucket, key_id, secret, host=None):
        self.bucket, self.key_id, self.secret = bucket, key_id, secret
        self.host = host or f"{account_id}.r2.cloudflarestorage.com"

    # ---------------------------------------------------------------- signing (AWS SigV4)
    def _key(self, day):
        k = _h(("AWS4" + self.secret).encode(), day)
        for part in (REGION, SERVICE, "aws4_request"):
            k = _h(k, part)
        return k

    def _uri(self, key):
        return "/" + _q(self.bucket) + ("/" + _q(key, safe="-_.~/") if key else "")

    def presign(self, key, expires=3600, method="GET", now=None, uri=None, region=REGION):
        """Time-limited link anyone can open (Instagram/Facebook fetch the video through it). Max 7 days."""
        t = now or datetime.datetime.now(datetime.timezone.utc)
        stamp, day = t.strftime("%Y%m%dT%H%M%SZ"), t.strftime("%Y%m%d")
        scope = f"{day}/{region}/{SERVICE}/aws4_request"
        q = {"X-Amz-Algorithm": "AWS4-HMAC-SHA256", "X-Amz-Credential": f"{self.key_id}/{scope}", "X-Amz-Date": stamp,
             "X-Amz-Expires": str(min(int(expires), 604800)), "X-Amz-SignedHeaders": "host"}
        qs = "&".join(f"{_q(k)}={_q(v)}" for k, v in sorted(q.items()))
        uri = uri or self._uri(key)
        creq = "\n".join([method, uri, qs, f"host:{self.host}\n", "host", "UNSIGNED-PAYLOAD"])
        sts = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(creq.encode()).hexdigest()])
        k = _h(("AWS4" + self.secret).encode(), day)
        for part in (region, SERVICE, "aws4_request"):
            k = _h(k, part)
        sig = hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()
        return f"https://{self.host}{uri}?{qs}&X-Amz-Signature={sig}"

    def _request(self, method, key="", query=None, body=None, payload_hash=None, headers=None, timeout=300):
        t = datetime.datetime.now(datetime.timezone.utc)
        stamp, day = t.strftime("%Y%m%dT%H%M%SZ"), t.strftime("%Y%m%d")
        ph = payload_hash or hashlib.sha256(body if isinstance(body, bytes) else b"").hexdigest()
        qs = "&".join(f"{_q(k)}={_q(str(v))}" for k, v in sorted((query or {}).items()))
        uri = self._uri(key)
        canon = f"host:{self.host}\nx-amz-content-sha256:{ph}\nx-amz-date:{stamp}\n"
        signed = "host;x-amz-content-sha256;x-amz-date"
        creq = "\n".join([method, uri, qs, canon, signed, ph])
        scope = f"{day}/{REGION}/{SERVICE}/aws4_request"
        sts = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(creq.encode()).hexdigest()])
        sig = hmac.new(self._key(day), sts.encode(), hashlib.sha256).hexdigest()
        hdr = {"x-amz-content-sha256": ph, "x-amz-date": stamp,
               "Authorization": f"AWS4-HMAC-SHA256 Credential={self.key_id}/{scope}, SignedHeaders={signed}, Signature={sig}", **(headers or {})}
        req = urllib.request.Request(f"https://{self.host}{uri}" + (f"?{qs}" if qs else ""), data=body, method=method, headers=hdr)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            msg = e.read()[:300].decode("utf-8", "replace")
            raise RuntimeError(f"R2 {method} failed (HTTP {e.code}): {re.sub(r'<[^>]+>', ' ', msg).strip()[:160]}") from None

    # ---------------------------------------------------------------- operations
    def put(self, key, file_path, content_type):
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        with open(file_path, "rb") as f:
            data = f.read()
        self._request("PUT", key, body=data, payload_hash=h.hexdigest(), headers={"Content-Type": content_type, "Content-Length": str(len(data))})
        return key

    def get(self, key, dest):
        _, data = self._request("GET", key, timeout=600)
        with open(dest, "wb") as f:
            f.write(data)

    def delete(self, key):
        self._request("DELETE", key)

    def list(self, prefix=""):
        _, xml = self._request("GET", "", query={"list-type": "2", "prefix": prefix, "max-keys": "1000"})
        ns = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
        return [k.text for k in ElementTree.fromstring(xml).findall(".//s:Contents/s:Key", ns)]


def r2_from_cloud(db):
    """The R2 bucket set up on the site, only once the cloud has checked that it works. None otherwise."""
    try:
        st = (db.select("settings", "select=value&key=eq.r2") or [{}])[0].get("value") or {}
        if st.get("state") != "ok" or not st.get("account_id") or not st.get("bucket"):
            return None
        rows = {r["key"]: r.get("value") for r in db.select("secrets", "select=key,value&key=in.(r2_access_key_id,r2_secret_access_key)")}
        if not rows.get("r2_access_key_id") or not rows.get("r2_secret_access_key"):
            return None
        return R2(st["account_id"], st["bucket"], rows["r2_access_key_id"], rows["r2_secret_access_key"])
    except Exception:  # noqa: BLE001  (no R2 -> Supabase)
        return None


PLAY_DAYS = 7


def put_reel(db, prefix, video_path, thumb_path, log=print):
    """Upload the posting copy (R2 if set up, else Supabase) and the thumbnail (always Supabase: tiny, used by the dashboards).
    Returns the fields to merge into the reel's output."""
    r2 = r2_from_cloud(db); out = {}
    if r2:
        try:
            key = f"{prefix}/ig.mp4"; r2.put(key, video_path, "video/mp4")
            until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=PLAY_DAYS)
            out = {"store": "r2", "video": key, "ig_video": key, "play_url": r2.presign(key, PLAY_DAYS * 86400),
                   "play_until": until.strftime("%Y-%m-%dT%H:%M:%SZ")}
        except Exception as e:  # noqa: BLE001  (R2 down or key revoked: never lose the reel)
            log(f"R2 upload failed ({str(e)[:120]}); keeping the reel in Supabase for now")
            out = {}
    if not out:
        db.upload(f"{prefix}/ig.mp4", video_path, "video/mp4")
        out = {"store": "sb", "video": f"{prefix}/ig.mp4", "ig_video": f"{prefix}/ig.mp4"}
    db.upload(f"{prefix}/thumb.jpg", thumb_path, "image/jpeg")
    out.update(storage_prefix=prefix, thumb=f"{prefix}/thumb.jpg", stored_at=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    return out


def download(db, output, dest, path=None):
    """The reel's video, wherever it is stored (used when a reel is revised on a Mac that no longer has the files)."""
    p = path or output.get("ig_video") or output.get("video")
    if output.get("store") == "r2":
        r2 = r2_from_cloud(db)
        if not r2:
            raise RuntimeError("the reel is in R2 but R2 is not set up on the site any more")
        return r2.get(p, dest)
    return db.download(p, dest)

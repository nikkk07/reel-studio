"""AI thumbnails for YouTube Shorts via ThumbAPI (optional, off unless the owner switches it on in Studio Settings).
Free plan: 50 credits a month = 5 thumbnails (10 credits each), watermarked. The image is saved next to the reel's
cover (thumbnails are kept after the video is deleted); the cloud poster sets it on YouTube after the upload.
API: POST https://api.thumbapi.dev/v1/generate (x-api-key) -> {"image": "data:image/webp;base64,..."}  (thumbapi.dev/docs)"""
import base64, io
from pathlib import Path
import httpx
from . import config, studio

API = config.get("THUMBAPI_URL", "https://api.thumbapi.dev/v1/generate")


def wanted(db, brand_id: str) -> bool:
    if not (studio.thumbs_on() and studio.get("THUMBAPI_KEY")):
        return False
    try:
        k = "youtube_status" if brand_id == "weone" else f"youtube_status@{brand_id}"
        rows = db.select("settings", f"select=value&key=eq.{k}")
        return (rows[0]["value"] if rows else {}).get("state") in ("connected", "expired", "error")
    except Exception:  # noqa: BLE001
        return False


def make(db, brand_id: str, prefix: str, title: str, workdir: Path, log=print):
    """Returns the stored thumbnail path, or None (never raises: a thumbnail is a bonus, not part of the reel)."""
    if not wanted(db, brand_id):
        return None
    try:
        r = httpx.post(API, headers={"x-api-key": studio.get("THUMBAPI_KEY"), "Content-Type": "application/json"},
                       json={"title": str(title)[:120], "format": "youtube", "imageStyle": "faceless"}, timeout=90)
        if r.status_code != 200:
            log(f"ThumbAPI did not make a thumbnail (HTTP {r.status_code}: {r.text[:120]})"); return None
        data = str(r.json().get("image") or "")
        raw = base64.b64decode(data.split(",", 1)[1] if "," in data else data)
        from PIL import Image
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        if im.size != (1280, 720):
            im = im.resize((1280, 720))
        out = workdir / "yt_thumb.jpg"; out.parent.mkdir(parents=True, exist_ok=True)
        im.save(out, "JPEG", quality=88)                     # YouTube takes JPEG/PNG up to 2 MB
        path = f"{prefix}/yt_thumb.jpg"
        db.upload(path, out, "image/jpeg")
        return path
    except Exception as e:  # noqa: BLE001
        log(f"ThumbAPI thumbnail skipped ({str(e)[:120]})")
        return None

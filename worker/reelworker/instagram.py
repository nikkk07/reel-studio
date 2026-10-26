"""Instagram preparation on the Mac. Posting itself runs in the cloud (Supabase Edge Function ig-poster + pg_cron),
so approved reels go out even while the Mac is off.

For every reel that reaches Review the Mac prepares, right away:
  * ig_caption - Claude's caption written for Instagram's 2026 ranking (hook + search keywords, facts, share prompt, <= 5 hashtags)
  * ig_video   - an Instagram-spec copy of the reel (H.264, AAC 128 kbps / 48 kHz, moov first) in storage
"""
import json, logging, re, shutil, subprocess, time
from datetime import datetime, timedelta, timezone
from . import config, brain, schemas
from .runner import QuotaError

log = logging.getLogger("reelworker")


def _setting(db, key):
    rows = db.select("settings", f"select=value&key=eq.{key}")
    return rows[0]["value"] if rows else {}


def handle(db):
    u = _setting(db, "instagram_status").get("username")
    return f"@{u}" if u else "We One Aviation"


def make_caption(db, job_id, output, brand_id="weone"):
    """Hinglish caption + hashtags from the reel's own verified facts (Claude, one short call)."""
    spec_p = config.JOBS_DIR / job_id / "spec.json"
    if spec_p.exists():
        spec = json.loads(spec_p.read_text(encoding="utf-8"))
        reel = {"title": spec.get("title"), "narration": [p.get("caption", "").replace("*", "") for g in spec.get("groups", []) for p in g.get("phrases", [])],
                "facts": [{"claim": c.get("claim"), "source": c.get("source_url")} for c in spec.get("claims", [])]}
    else:
        reel = {"title": output.get("title"), "narration": [l.get("text", "").replace("*", "") for l in output.get("script", [])],
                "facts": [{"claim": c.get("claim"), "source": c.get("source_url")} for c in output.get("claims", [])]}
    from . import brand as brandmod
    prof = brandmod.load(db, brand_id)
    if brandmod.legacy(prof):
        h = handle(db)
        text = brain.prompt("caption", reel=json.dumps(reel, ensure_ascii=False, indent=1), handle=h); own = OUR
    else:                                    # this account's own voice, handle and tag; never We One's
        own = (_setting(db, f"instagram_status@{brand_id}").get("username") or prof.get("instagram") or "").lstrip("@"); h = f"@{own}" if own else prof["name"]
        tag = re.sub(r"[^a-z0-9]", "", prof["name"].lower())
        text = brain.prompt("caption_brand", brand_name=prof["name"], brand_brief=brandmod.brief(prof, full=False), reel=json.dumps(reel, ensure_ascii=False, indent=1),
                            handle=h, language=prof.get("language", "english").title(), brand_tag=tag)
    from . import agents
    import types
    actx = types.SimpleNamespace(db=db, job={"id": job_id, "brand": brand_id}, id=job_id, dir=config.JOBS_DIR / job_id, log=lambda *a, **k: None)
    res = agents.call(actx, "caption_writer", text, schemas.CAPTION, [], timeout=300, name="caption")
    return compose(res["caption"], res["hashtags"], own=own or "-")


OUR = "we_one_aviation"
_URL = re.compile(r"((?i:https?://|www\.)\S+|\b[\w-]+\.(?:com|in|net|org|io|ly|co|xyz|link|me)(?:/\S*)?\b)")
_MAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
_PHONE = re.compile(r"(?<![\d+])(?:\+\d{1,3}[\s-]?\d[\d\s-]{7,}\d|(?:0|91[\s-]?)?[6-9]\d{4}[\s-]?\d{5})(?!\d)")


def clean_caption(text, own=OUR):
    """No links (they don't work in captions and are a spam signal), no e-mails/phones, no other accounts' handles."""
    text = _URL.sub("", _MAIL.sub("", str(text)))
    text = _PHONE.sub("", text)
    text = re.sub(r"@(\w[\w.]*)", lambda m: m.group(0) if m.group(1).lower().rstrip(".") == own.lower() else m.group(1), text)
    return re.sub(r"[ \t]{2,}", " ", text)


def compose(caption, hashtags, own=OUR):
    tags, seen = [], set()
    caption = clean_caption(caption, own)
    inline = re.findall(r"#\w+", caption)
    for t in inline:                      # hashtags written inside the text count toward Instagram's limit of 5
        seen.add(t.lower())
    if len(inline) > 5:
        for t in inline[5:]:
            caption = caption.replace(t, t[1:], 1)
        inline = inline[:5]
    for t in hashtags:
        t = "#" + re.sub(r"[^\w]", "", str(t).lstrip("#"))
        if len(t) > 1 and t.lower() not in seen:
            seen.add(t.lower()); tags.append(t)
    text = re.sub(r"\n{3,}", "\n\n", str(caption).strip())
    full = f"{text}\n\n{' '.join(tags[:max(0, 5 - len(inline))])}".rstrip()      # Instagram allows at most 5 hashtags (since Dec 2025)
    return full[:2200]


# ------------------------------------------------------------------ the video Instagram receives
def _probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,width,height",
                        "-of", "json", str(path)], capture_output=True, text=True, timeout=60)
    return json.loads(r.stdout or "{}")


def prepare_video(db, job_id, output):
    """Instagram spec: MP4, H.264/HEVC, AAC <=48 kHz (128 kbps), moov first, 3 s - 15 min, <= 300 MB."""
    work = config.JOBS_DIR / job_id / "ig"; work.mkdir(parents=True, exist_ok=True)
    src = config.JOBS_DIR / job_id / "render" / "reel.mp4"
    if not src.exists():
        src = work / "src.mp4"
        db.download(output["video"], src)
    dst = work / "ig.mp4"
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(dst)],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"could not prepare the video: {r.stderr[-200:]}")
    info = _probe(dst); dur = float(info.get("format", {}).get("duration", 0))
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    if not 3 <= dur <= 900:
        raise RuntimeError(f"reel length {dur:.1f} s is outside Instagram's 3 s - 15 min")
    if v.get("codec_name") not in ("h264", "hevc") or (v.get("width") or 0) > 1920:
        raise RuntimeError("video format not accepted by Instagram")
    if dst.stat().st_size > 300e6:
        raise RuntimeError("video is larger than 300 MB")
    db.upload(f"{job_id}/ig.mp4", dst, "video/mp4")
    shutil.rmtree(work, ignore_errors=True)
    return f"{job_id}/ig.mp4"



def prepare(db, job):
    """Fill in whatever is missing (Instagram file, caption). Each part is independent, so a Claude limit never blocks the file."""
    out = dict(job.get("output") or {}); add = {}; errors = []
    if not out.get("ig_video") and out.get("video"):
        try:
            add["ig_video"] = prepare_video(db, job["id"], out)
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    if not out.get("ig_caption"):
        try:
            add["ig_caption"] = make_caption(db, job["id"], out, job.get("brand") or "weone")
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    if add:
        fresh = db.select("jobs", f"select=output&id=eq.{job['id']}")       # merge into the latest output (the site may have changed it)
        cur = (fresh[0]["output"] if fresh else out) or {}
        db.update("jobs", f"id=eq.{job['id']}", {"output": {**cur, **add}})
        job["output"] = {**cur, **add}
    if errors:
        raise errors[0]
    return add


def loop(db, stop):
    """Catch-up: prepares reels that missed it (made before this feature, a Claude limit, or the Mac was busy/off)."""
    failures, quiet_until = {}, 0.0
    while not stop.is_set():
        try:
            since = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
            rows = db.select("jobs", "select=id,brand,output,status&kind=eq.reel&status=in.(review,done)"
                                     f"&updated_at=gt.{since}&or=(output->>ig_caption.is.null,output->>ig_video.is.null)&limit=5")
            for job in rows:
                o = job["output"] or {}
                if failures.get(job["id"], 0) >= 3 or o.get("ig", {}).get("state") in ("posted", "posting"):
                    continue
                if o.get("ig_video") and not o.get("ig_caption") and time.time() < quiet_until:
                    continue          # only the caption is missing and Claude is at its limit: try after the reset
                try:
                    if prepare(db, job):
                        log.info("[%s] Instagram file/caption ready", job["id"][:8])
                except QuotaError as e:
                    quiet_until = time.time() + 1800
                    log.info("[%s] caption waits for Claude's limit to reset (%s)", job["id"][:8], str(e)[:80])
                except Exception as e:  # noqa: BLE001
                    failures[job["id"]] = failures.get(job["id"], 0) + 1
                    log.warning("[%s] Instagram preparation failed (%s)", job["id"][:8], str(e)[:200])
        except Exception as e:  # noqa: BLE001
            log.warning("instagram prep loop: %s", str(e)[:300])
        stop.wait(float(config.get("REELSTUDIO_IG_TICK", "60")))

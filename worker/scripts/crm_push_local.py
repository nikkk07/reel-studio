"""Send a Reel made on this Mac (make_local.py) to CRM > Content as an ad video, after the owner approved it.

    ~/ReelStudio/.venv/bin/python worker/scripts/crm_push_local.py <job id> [--item <CRM content item id>]

--item attaches the video to the CRM item the ad plan made for it (e.g. the v5 creative), so the Meta ad build uses it;
without --item the CRM makes a new Ad creatives item. The master file is re-encoded to fit the CRM's 50 MB limit
(same 1080x1920, H.264 + AAC), a cover is taken from the hook, both go straight to CRM storage by one-time links.
Uses CRM_CONTENT_TOKEN from the Mac's .env; nothing is printed of it.
"""
import argparse, json, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reelworker import config, crm  # noqa: E402

LIMIT_MB = 45


def master(jid: str) -> Path:
    try:
        p = Path(json.loads((config.JOBS_DIR / jid / "render" / "result.json").read_text())["delivery"])
        if p.exists():
            return p
    except Exception:  # noqa: BLE001
        pass
    found = sorted((config.APP / "deliverables" / "local").glob(f"*_{jid[:8]}.mp4"), key=lambda p: p.stat().st_mtime)
    if not found:
        raise SystemExit(f"no finished video for job {jid}")
    return found[-1]


def duration(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    return float(r.stdout.strip())


def fit(src: Path, dst: Path, secs: float) -> Path:
    """1080x1920 H.264 under LIMIT_MB: the bitrate is worked out from the length, lowered once more if still too big."""
    if src.stat().st_size <= LIMIT_MB * 1e6:
        return src
    kbps = int(LIMIT_MB * 8e3 * 0.9 / secs) - 160
    for _ in range(3):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-c:v", "libx264", "-preset", "medium", "-b:v", f"{kbps}k",
                        "-maxrate", f"{int(kbps * 1.3)}k", "-bufsize", f"{kbps * 2}k", "-pix_fmt", "yuv420p", "-r", "30",
                        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dst)], check=True)
        if dst.stat().st_size <= LIMIT_MB * 1e6:
            return dst
        kbps = int(kbps * 0.85)
    raise SystemExit("could not fit the video under the CRM limit")


def main():
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("job"); a.add_argument("--item", type=int)
    o = a.parse_args()
    if not crm.linked():
        raise SystemExit("the CRM link is not set on this Mac (CRM_CONTENT_URL / CRM_CONTENT_TOKEN in .env)")
    src = master(o.job); secs = duration(src)
    out_dir = config.JOBS_DIR / o.job / "crm"; out_dir.mkdir(parents=True, exist_ok=True)
    video = fit(src, out_dir / "video.mp4", secs)
    cover = crm._cover(video, out_dir / "cover.jpg")
    print(f"video {video.stat().st_size / 1e6:.1f} MB, {secs:.1f} s", flush=True)
    item = o.item
    if item:
        r = crm.call("update", id=item, status="producing", studio_job=o.job)
        if isinstance(r, dict) and r.get("ok") is False:
            print(f"note: CRM update: {r.get('detail')}", flush=True)
    else:
        title, script, summary = crm._ad_text(o.job)
        r = crm.call("adopt", studio_job=o.job, title=title, script=script, summary=summary)
        if not r.get("ok"):
            raise SystemExit(f"CRM adopt: {r.get('detail')}")
        item = int(r["id"])
    links = crm.call("video_upload", id=int(item))
    crm._put(links["video"]["url"], video, "video/mp4")
    crm._put(links["thumb"]["url"], cover, "image/jpeg")
    r = crm.call("video_done", id=int(item), seconds=round(secs, 2))
    if not r.get("ok"):
        raise SystemExit(f"CRM video_done: {r.get('detail')}")
    st = crm._vstate(); st[o.job] = {"item": item, "done": True, "at": datetime.now(timezone.utc).isoformat()}; crm._save_vstate(st)
    print(f"sent to CRM > Content > Ad creatives: item {item} is ready", flush=True)


if __name__ == "__main__":
    main()

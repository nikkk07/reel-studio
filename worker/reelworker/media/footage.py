"""Stock footage: Pexels + Pixabay + Coverr search, contact sheets for Claude, download, 9:16 shot prep."""
import concurrent.futures as cf, json, subprocess, time, urllib.parse
from pathlib import Path
import httpx
from PIL import Image, ImageDraw, ImageFont
from .. import config, studio

UA = {"User-Agent": "WeOneAviation-ReelStudio/1.0"}


def search(query: str, per_source=6) -> list:
    out = []
    key = studio.get("PEXELS_API_KEY")
    if key:
        try:
            r = httpx.get("https://api.pexels.com/videos/search", params={"query": query, "per_page": per_source},
                          headers={"Authorization": key, **UA}, timeout=25)
            for v in r.json().get("videos", []):
                files = [f for f in v["video_files"] if f.get("height") and f.get("link")]
                # smallest file that is still >= 1080 on the short side, else the largest
                good = sorted([f for f in files if min(f["width"], f["height"]) >= 1080], key=lambda f: f["width"] * f["height"])
                b = good[0] if good else max(files, key=lambda f: f["width"] * f["height"])
                out.append(dict(src="pexels", id=v["id"], page=v["url"], dur=v["duration"], w=b["width"], h=b["height"],
                                dl=b["link"], thumb=v["image"], author=v["user"]["name"], licence="Pexels License"))
        except Exception:  # noqa: BLE001
            pass
    key = studio.get("PIXABAY_API_KEY")
    if key:
        try:
            r = httpx.get("https://pixabay.com/api/videos/", params={"key": key, "q": query, "per_page": max(3, per_source)},
                          headers=UA, timeout=25)
            for v in r.json().get("hits", []):
                vids = v["videos"]
                b = vids.get("large") if vids.get("large", {}).get("url") else vids["medium"]
                out.append(dict(src="pixabay", id=v["id"], page=v["pageURL"], dur=v["duration"], w=b["width"], h=b["height"], dl=b["url"],
                                thumb=b.get("thumbnail") or vids["medium"].get("thumbnail"), author=v["user"], licence="Pixabay Content License"))
        except Exception:  # noqa: BLE001
            pass
    key = studio.get("COVERR_API_KEY")
    if key and not _coverr_paused():
        try:                     # free API (demo keys: 50 searches an hour); https://api.coverr.co/docs/videos/
            r = httpx.get("https://api.coverr.co/videos", params={"query": query, "page_size": max(3, per_source), "urls": "true"},
                          headers={"Authorization": f"Bearer {key}", **UA}, timeout=25)
            if r.status_code == 429:
                _COVERR["paused_until"] = time.time() + 900
            for v in (r.json().get("hits", []) if r.status_code == 200 else []):
                u = v.get("urls") or {}
                dl = u.get("mp4_download") or u.get("mp4")
                if not dl:
                    continue
                out.append(dict(src="coverr", id=v["id"], page="https://coverr.co", dur=round(float(v.get("duration") or 0), 1),
                                w=int(v.get("max_width") or 1920), h=int(v.get("max_height") or 1080), dl=dl,
                                thumb=v.get("thumbnail") or v.get("poster"), author="Coverr", licence="Coverr License"))
        except Exception:  # noqa: BLE001
            pass
    return _mix(out)


def _mix(cands: list) -> list:
    """Round-robin by source (best of each first), so a cap on the list keeps every library in the running."""
    by = {}
    for c in cands:
        by.setdefault(c["src"], []).append(c)
    lists = list(by.values()); out = []
    for i in range(max((len(x) for x in lists), default=0)):
        out += [x[i] for x in lists if i < len(x)]
    return out


_COVERR = {"paused_until": 0.0}


def _coverr_paused():
    return time.time() < _COVERR["paused_until"]


def contact_sheets(shots: list, cands: dict, folder: Path, font_path: str):
    """One sheet per shot: tiles labelled <shot>-<n>. Returns {shot_id: sheet_path}."""
    folder.mkdir(parents=True, exist_ok=True); thumbs = folder / "thumbs"; thumbs.mkdir(exist_ok=True)
    jobs = [(sid, i, c) for sid, lst in cands.items() for i, c in enumerate(lst)]

    def dl(j):
        sid, i, c = j; p = thumbs / f"{sid}-{i}.jpg"
        if not p.exists():
            try:
                if str(c["thumb"]).startswith("file://"):
                    p.write_bytes(Path(c["thumb"][7:]).read_bytes())
                else:
                    p.write_bytes(httpx.get(c["thumb"], headers=UA, timeout=20, follow_redirects=True).content)
            except Exception:  # noqa: BLE001
                return
    with cf.ThreadPoolExecutor(12) as ex:
        list(ex.map(dl, jobs))
    try:
        f = ImageFont.truetype(font_path, 18)
    except Exception:  # noqa: BLE001
        f = ImageFont.load_default()
    out = {}
    for sid, lst in cands.items():
        W, H, cols = 360, 203, 4; rows = (len(lst) + cols - 1) // cols or 1
        S = Image.new("RGB", (W * cols, (H + 26) * rows), (0, 0, 0)); d = ImageDraw.Draw(S)
        for i, c in enumerate(lst):
            X, Y = (i % cols) * W, (i // cols) * (H + 26)
            d.text((X + 4, Y + 3), f"{sid}-{i}  {c['w']}x{c['h']}  {c['dur']}s", fill=(255, 220, 0), font=f)
            try:
                im = Image.open(thumbs / f"{sid}-{i}.jpg").convert("RGB"); im.thumbnail((W, H)); S.paste(im, (X, Y + 26))
            except Exception:  # noqa: BLE001
                pass
        p = folder / f"sheet_{sid}.jpg"; S.save(p, quality=82); out[sid] = str(p)
    return out


def download(c: dict, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.with_suffix(".ok").exists():
        return dest
    if str(c["dl"]).startswith("file://"):          # local footage library
        return Path(c["dl"][7:])
    with httpx.stream("GET", c["dl"], headers=UA, timeout=180, follow_redirects=True) as r:
        r.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in r.iter_bytes(1 << 20):
                fh.write(chunk)
    dest.with_suffix(".ok").write_text("ok")
    return dest


def prep_shot(src: Path, out: Path, in_point: float, dur: float, focus_x: float = 0.5):
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
                           capture_output=True, text=True).stdout.strip()
    total = float(probe or 0)
    if total and in_point + dur > total:
        in_point = max(0.0, total - dur)
    fx = max(0.0, min(1.0, focus_x))
    vf = ("fps=60,scale=w='if(gt(iw/ih,1296/2304),-2,1296)':h='if(gt(iw/ih,1296/2304),2304,-2)':flags=lanczos,"
          f"crop=1296:2304:x='(iw-ow)*{fx}':y='(ih-oh)/2',eq=contrast=1.06:saturation=1.0,format=yuv420p")
    loop = ["-stream_loop", "-1"] if total and total < dur else []
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", *loop, "-ss", f"{in_point:.2f}", "-t", f"{dur:.2f}", "-i", str(src), "-vf", vf, "-an",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", str(out)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"shot prep failed: {r.stderr[-400:]}")
    return out


def credits(picked: dict) -> list:
    return [{"shot": sid, "source": c["src"], "author": c["author"], "page": c["page"], "licence": c["licence"]} for sid, c in picked.items()]

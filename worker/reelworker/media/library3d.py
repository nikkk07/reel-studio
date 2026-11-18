"""3D Library Keeper: our own 3D/Blender clips, offered to the Footage Picker before stock footage and used as ad backgrounds.

Folder: REELSTUDIO_3D_DIR in ~/ReelStudio/.env (default ~/ReelStudio/library/3d). Name each clip by what it shows
("cessna-172-takeoff-runway.mp4"); a .txt with the same name adds more words. The index (size, length, words, a thumbnail)
is kept in ~/ReelStudio/state and only new or changed clips are probed, so a large folder costs nothing after the first scan."""
import hashlib, json, re, subprocess, time
from pathlib import Path
from .. import config

EXT = {".mp4", ".mov", ".m4v"}
STOP = {"a", "an", "the", "of", "in", "on", "at", "to", "and", "or", "for", "with", "by", "from", "is", "shot", "clip", "video",
        "final", "render", "v1", "v2", "v3", "4k", "hd", "1080p", "copy"}
_CACHE = {"at": 0.0, "items": []}


def words(text: str) -> set:
    out = set()
    for w in re.split(r"[^a-z0-9]+", str(text).lower()):
        if len(w) < 2 or w in STOP or w.isdigit() and len(w) > 4:
            continue
        out.add(w[:-1] if len(w) > 4 and w.endswith("s") else w)
    return out


def _state():
    return config.APP / "state" / "library3d.json"


def _probe(p: Path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
                        "-of", "json", str(p)], capture_output=True, text=True, timeout=60)
    j = json.loads(r.stdout or "{}"); s = (j.get("streams") or [{}])[0]
    return float((j.get("format") or {}).get("duration") or 0), int(s.get("width") or 0), int(s.get("height") or 0)


def _thumb(p: Path, key: str, dur: float) -> str:
    out = config.APP / "state" / "thumbs3d" / f"{key}.jpg"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{min(1.0, dur / 3):.2f}", "-i", str(p), "-frames:v", "1",
                        "-vf", "scale=640:-2", str(out)], capture_output=True, timeout=60)
    return str(out)


def index(max_age=600) -> list:
    """Every usable clip in the folder (cached for 10 minutes; probes only new/changed files)."""
    if time.time() - _CACHE["at"] < max_age:
        return _CACHE["items"]
    root = config.LIBRARY_3D
    try:
        old = {i["path"]: i for i in json.loads(_state().read_text())}
    except Exception:  # noqa: BLE001
        old = {}
    items = []
    if root.exists():
        for p in sorted(root.rglob("*")):
            if p.suffix.lower() not in EXT or p.name.startswith("."):
                continue
            st = p.stat(); prev = old.get(str(p))
            side = p.with_suffix(".txt"); extra = side.read_text(encoding="utf-8", errors="ignore")[:500] if side.exists() else ""
            if prev and prev["mtime"] == st.st_mtime and prev["size"] == st.st_size and prev.get("extra") == extra:
                items.append(prev); continue
            try:
                dur, w, h = _probe(p)
            except Exception:  # noqa: BLE001
                continue
            if dur < 1 or not w:
                continue
            key = hashlib.sha1(str(p).encode()).hexdigest()[:12]
            rel = str(p.relative_to(root))
            items.append({"path": str(p), "key": key, "mtime": st.st_mtime, "size": st.st_size, "extra": extra, "dur": round(dur, 1),
                          "w": w, "h": h, "words": sorted(words(rel) | words(extra)), "thumb": _thumb(p, key, dur)})
        _state().parent.mkdir(parents=True, exist_ok=True)
        _state().write_text(json.dumps(items))
    _CACHE.update(at=time.time(), items=items)
    return items


def search(query: str, n=4) -> list:
    """Our clips that match the shot's search words, best first, as footage candidates (same shape as Pexels/Pixabay)."""
    q = words(query)
    if not q:
        return []
    scored = []
    for it in index():
        hit = len(q & set(it["words"]))
        if hit:
            scored.append((hit / len(q), it))
    scored.sort(key=lambda x: -x[0])
    return [dict(src="ours", id=it["key"], page="", dur=it["dur"], w=it["w"], h=it["h"], dl="file://" + it["path"],
                 thumb="file://" + it["thumb"], author="We One Aviation 3D", licence="Our own")
            for s, it in scored[:n] if s >= 0.34]


def frame(query: str, out: Path):
    """A still from the best matching clip (an ad picture's background), or None."""
    hits = search(query, 1)
    if not hits:
        return None
    src = hits[0]["dl"][7:]; dur = hits[0]["dur"]
    out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{dur / 2:.2f}", "-i", src, "-frames:v", "1", "-q:v", "2", str(out)],
                       capture_output=True, timeout=60)
    return out if r.returncode == 0 and out.exists() else None

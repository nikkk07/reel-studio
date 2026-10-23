"""Run the real worker loop against a local stack (PostgREST + storage stand-in) with mock brain and local clips.
    REELSTUDIO_ROOT=<prepared root> SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... python tests/run_worker_local.py <clips_dir>
"""
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("REELSTUDIO_BRAIN", "mock"); os.environ.setdefault("REELSTUDIO_TAKES", "1"); os.environ.setdefault("REELSTUDIO_DEVICE", "cpu")
from reelworker import worker, config  # noqa: E402
from reelworker.media import footage  # noqa: E402
CLIPS = sorted(Path(sys.argv[1]).glob("*.mp4"))
def fake_search(query, per_source=6):
    words = query.lower().split(); out = []
    for p in sorted(CLIPS, key=lambda p: -sum(w[:4] in p.stem.lower() for w in words))[:3]:
        th = config.ROOT / "thumbs" / f"{p.stem}.jpg"; th.parent.mkdir(exist_ok=True)
        if not th.exists(): os.system(f'ffmpeg -v error -y -ss 1 -i "{p}" -frames:v 1 -vf scale=360:-2 "{th}"')
        out.append(dict(src="local", id=p.stem, page="https://example.com/" + p.stem, dur=8, w=1080, h=1920, dl=f"file://{p}", thumb=f"file://{th}", author="test", licence="test"))
    return out
footage.search = fake_search
worker.main()

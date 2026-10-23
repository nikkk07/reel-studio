"""End-to-end test without Supabase or Claude: FakeDB + mock brain + local footage, real voice/render pipeline.

    python tests/e2e_local.py <test_root> <clips_dir> <voice_ref.wav> <roomtone.wav> <fonts_dir> <logo.png> <lexicon.json> [utmos.pt]
"""
import json, os, shutil, sys, time, uuid
from pathlib import Path

root, clips, ref, room, fonts, logo, lex = map(Path, sys.argv[1:8])
utmos = Path(sys.argv[8]) if len(sys.argv) > 8 else None
for d in ("library/voice/refs", "library/brand", "library/models"):
    (root / d).mkdir(parents=True, exist_ok=True)
shutil.copy(ref, root / "library/voice/refs/female.wav"); shutil.copy(room, root / "library/voice/refs/roomtone.wav")
shutil.copy(lex, root / "library/voice/pronunciation_lexicon.json"); shutil.copy(logo, root / "library/brand/logo.png")
if not (root / "library/fonts").exists():
    shutil.copytree(fonts, root / "library/fonts")
if utmos and not (root / "library/models" / utmos.name).exists():
    os.symlink(utmos, root / "library/models" / utmos.name)
os.environ.update(REELSTUDIO_ROOT=str(root), REELSTUDIO_BRAIN="mock", REELSTUDIO_TAKES=os.environ.get("REELSTUDIO_TAKES", "1"),
                  REELSTUDIO_DEVICE="cpu", REELSTUDIO_RENDER_PROCS="2")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reelworker import worker, config                    # noqa: E402
from reelworker.media import footage                     # noqa: E402
from reelworker.jobs import suggest, optimize, reel      # noqa: E402


class FakeDB:
    def __init__(self):
        self.rows = {"jobs": {}, "job_events": [], "workers": {}}; self.storage = {}
    def rpc(self, fn, **a):
        return True
    def select(self, table, query=""):
        return [{"status": j["status"], "output": j.get("output") or {}} for j in self.rows["jobs"].values()] if table == "jobs" else []
    def insert(self, table, row, upsert=False):
        if table == "job_events":
            self.rows["job_events"].append(row); print("   event:", row["level"], row["message"][:150], flush=True)
        return [row]
    def update(self, table, match, values):
        jid = match.split("eq.")[1]; self.rows["jobs"].setdefault(jid, {}).update(values); return [values]
    def upload(self, path, file_path, ctype):
        self.storage[path] = Path(file_path).stat().st_size; return path


CLIPS = sorted(clips.glob("*.mp4"))
def fake_search(query, per_source=6):
    words = query.lower().split()
    ranked = sorted(CLIPS, key=lambda p: -sum(w[:4] in p.stem.lower() for w in words))
    out = []
    for i, p in enumerate(ranked[:3]):
        th = root / "thumbs" / f"{p.stem}.jpg"; th.parent.mkdir(exist_ok=True)
        if not th.exists():
            os.system(f'ffmpeg -v error -y -ss 1 -i "{p}" -frames:v 1 -vf scale=360:-2 "{th}"')
        out.append(dict(src="local", id=p.stem, page=str(p), dur=8, w=1080, h=1920, dl=f"file://{p}", thumb=f"file://{th}",
                        author="test", licence="test"))
    return out
footage.search = fake_search

db = FakeDB(); t_all = time.time()
for kind, handler, inp in [("suggest", suggest.run, {}), ("optimize", optimize.run, {"prompt": "plane par bijli"}),
                           ("reel", reel.run, {"concept": json.load(open(Path(__file__).parent / "fixtures/optimize.json"))})]:
    jid = str(uuid.uuid4()); job = {"id": jid, "kind": kind, "input": inp, "attempts": 1, "status": "running"}
    db.rows["jobs"][jid] = dict(job); t0 = time.time(); print(f"== {kind} {jid[:8]}", flush=True)
    worker.run_job(db, job, handler)
    row = db.rows["jobs"][jid]
    print(f"== {kind}: status={row.get('status')} in {time.time() - t0:.0f}s error={row.get('error')}", flush=True)
    assert row.get("status") in ("done", "review"), row
    if kind == "reel":
        out = row["output"]
        print(json.dumps({k: out.get(k) for k in ("title", "duration", "lufs", "true_peak", "delivery_mb", "weak_words", "new_english_words",
                                                  "archive_on_mac", "video")}, ensure_ascii=False, indent=1))
        print("qc:", out["qc"]); print("storage:", db.storage)
        assert out["video"] in db.storage and out["delivery_mb"] <= config.DELIVERY_MAX_MB
        assert -15.0 <= out["lufs"] <= -13.0 and out["true_peak"] <= -1.0
        print("REEL_DIR", config.JOBS_DIR / jid)
print(f"ALL OK in {time.time() - t_all:.0f}s")

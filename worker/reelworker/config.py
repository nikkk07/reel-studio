"""Runtime configuration. All secrets come from the .env file on the Mac (never printed, never committed)."""
import os, re, socket
from pathlib import Path

VERSION = "1.0.0"


def _load_env(path: Path) -> dict:
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*)", line)
            if m and not line.lstrip().startswith("#"):
                env[m.group(1)] = m.group(2).strip().strip('"\'')
    return env


# Runtime home is outside Desktop/Documents so the background service needs no macOS privacy permission.
# setup_mac.sh fills it from EduReelStudio/library (fonts, voice refs, lexicon, models, logo).
ROOT = Path(os.environ.get("REELSTUDIO_ROOT", Path.home() / "ReelStudio")).expanduser()
ENV = {**_load_env(ROOT / ".env"), **{k: v for k, v in os.environ.items() if k.startswith(("SUPABASE_", "PEXELS_", "PIXABAY_", "REELSTUDIO_", "TELEGRAM_"))}}


def get(key: str, default=None, required=False):
    v = ENV.get(key, default)
    if required and not v:
        raise RuntimeError(f"missing setting {key} in {ROOT / '.env'}")
    return v


SUPABASE_URL = (get("SUPABASE_URL") or "").rstrip("/")
SUPABASE_SERVICE_KEY = get("SUPABASE_SERVICE_ROLE_KEY")           # service key: Mac only
WORKER_ID = get("REELSTUDIO_WORKER_ID", socket.gethostname().split(".")[0] or "mac-mini")

APP = ROOT
JOBS_DIR = Path(get("REELSTUDIO_JOBS_DIR", APP / "jobs"))
DELIVERABLES = Path(get("REELSTUDIO_DELIVERABLES", APP / "deliverables"))
LOG_DIR = APP / "logs"
LIBRARY = ROOT / "library"
FONTS_DIR = Path(get("REELSTUDIO_FONTS_DIR", LIBRARY / "fonts"))
VOICE_DIR = Path(get("REELSTUDIO_VOICE_DIR", LIBRARY / "voice"))
LEXICON = VOICE_DIR / "pronunciation_lexicon.json"
LOGO = Path(get("REELSTUDIO_LOGO", LIBRARY / "brand/logo.png"))
MODELS_DIR = Path(get("REELSTUDIO_MODELS_DIR", LIBRARY / "models"))
UTMOS_CKPT = MODELS_DIR / "utmos22_strong_step7459_v1.pt"

DAILY_REEL_CAP = int(get("REELSTUDIO_DAILY_REEL_CAP", "3"))
TAKES_PER_GROUP = int(get("REELSTUDIO_TAKES", "3"))
VOICE_HI = get("REELSTUDIO_VOICE_HI", "v2")                         # v2 | v3 | hi  (media/voice.py MODELS; changeable in Studio Settings)
VOICE_EN = get("REELSTUDIO_VOICE_EN", "v2")
CLAUDE_BIN = get("REELSTUDIO_CLAUDE_BIN", "claude")
CLAUDE_MODEL = get("REELSTUDIO_CLAUDE_MODEL", "")                  # empty = Claude Code default
BRAIN_MODE = get("REELSTUDIO_BRAIN", "claude")                      # claude | mock (tests)
DEVICE = get("REELSTUDIO_DEVICE", "auto")                          # auto | mps | cpu
RENDER_PROCS = int(get("REELSTUDIO_RENDER_PROCS", "0"))            # 0 = auto
DELIVERY_MAX_MB = float(get("REELSTUDIO_DELIVERY_MAX_MB", "45"))   # Supabase free tier: 50 MB per file
BUCKET = "reels"

for d in (JOBS_DIR, LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)

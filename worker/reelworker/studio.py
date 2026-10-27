"""Settings the owner changes on the site (Studio Settings), read from the cloud database every 2 minutes:
the Pexels / Pixabay keys and the daily limit of new reels. The Mac's own .env is the fallback, so a removed or
unreachable cloud value never stops the studio. Keys are never logged or printed."""
import threading, time
from . import config

ENV_NAMES = {"PEXELS_API_KEY": "pexels", "PIXABAY_API_KEY": "pixabay"}
_lock = threading.Lock()
_state = {"db": None, "at": 0.0, "keys": {}, "cap": None, "voice": {}, "style": {}}
VOICE_MODELS = ("v2", "v3", "hi")     # 'hi' (V3 Hindi pack) for English = English read with a natural Indian accent
# delivery: (exaggeration, cfg_weight). Expressive = Chatterbox's own advice for more emotion: exaggeration ~0.7 with cfg ~0.3
# (lower cfg keeps the pace calm when exaggeration rises).
STYLES = {"natural": (0.5, 0.5), "expressive": (0.7, 0.3)}


def bind(db):
    _state["db"] = db; _state["at"] = 0.0


def refresh(force=False):
    db = _state["db"]
    if db is None or (not force and time.time() - _state["at"] < 120):
        return
    with _lock:
        if not force and time.time() - _state["at"] < 120:
            return
        try:
            rows = db.select("secrets", "select=key,value&key=in.(api_pexels,api_pixabay)")
            _state["keys"] = {r["key"][4:]: r["value"] for r in rows if r.get("value")}
            ws = db.select("settings", "select=value&key=eq.worker_settings")
            cap = (ws[0]["value"] if ws else {}).get("daily_cap")
            _state["cap"] = int(cap) if cap and 1 <= int(cap) <= 10 else None
            v = ws[0]["value"] if ws else {}
            _state["voice"] = {k: v.get(f"voice_{k}") for k in ("hi", "en") if v.get(f"voice_{k}") in VOICE_MODELS}
            _state["style"] = {k: v.get(f"style_{k}") for k in ("hi", "en") if v.get(f"style_{k}") in STYLES}
        except Exception:  # noqa: BLE001  (keep the last good values)
            pass
        _state["at"] = time.time()


def key(env_name):
    """(value, source): the key saved on the site, else the one in the Mac's .env."""
    refresh()
    v = _state["keys"].get(ENV_NAMES.get(env_name, ""))
    if v:
        return v, "cloud"
    v = config.get(env_name)
    return (v, "mac") if v else (None, None)


def get(env_name):
    return key(env_name)[0]


def daily_cap():
    refresh()
    return _state["cap"] or config.DAILY_REEL_CAP


def voice_model(lang):
    """Voice model for the Hinglish ('hi') or English ('en') voice: chosen in Studio Settings, else the studio default."""
    refresh()
    v = _state["voice"].get("hi" if lang == "hi" else "en")
    return v or (config.VOICE_HI if lang == "hi" else config.VOICE_EN)


def voice_style(lang):
    """(exaggeration, cfg_weight) of the Hinglish or English voice: chosen in Studio Settings, else natural."""
    refresh()
    return STYLES[_state.get("style", {}).get("hi" if lang == "hi" else "en") or "natural"]


def sources():
    return {ENV_NAMES[k]: key(k)[1] for k in ENV_NAMES}

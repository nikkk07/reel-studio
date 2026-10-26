"""Content mix for every account: `core` reels (the account's own services, courses and expertise: its identity)
and `trending` reels (what the audience talks about now: reach). Kept 50 / 50 over the last 10 reels."""
import re

LANES = ("core", "trending")
CORE_CATS = {"career", "syllabus", "exam", "service", "pricing", "how_we_work"}


def name(prof, lane):
    return ((prof or {}).get("lane_names") or {}).get(lane) or {"core": "Core", "trending": "Trending"}[lane]


def rules(prof, lane):
    p = prof or {}
    got = (p.get("lanes") or {}).get(lane)
    if got:
        return got
    if lane == "core":
        return (f"Core lane: teach or show ONE real thing from {p.get('name', 'the account')}'s own world: its services, its expertise, the "
                "questions its customers actually ask, the concepts they must understand before buying (see the knowledge profile and topic bank). "
                "The viewer should finish thinking: these people really know this. Never random trivia.")
    return ("Trending lane: topics this account's audience is talking about right now (news of the last 7 days, the season, rules changing on a date, "
            "viral questions, myths) with the highest chance to be sent and saved, still clearly right for this account.")


def of(lane, category=None, title="", prof=None):
    """Lane of a reel; older reels without one are classified by category and the account's core keywords."""
    if lane == "academy":
        return "core"
    if lane in LANES:
        return lane
    if category in CORE_CATS:
        return "core"
    words = (prof or {}).get("core_keywords") or []
    if words and re.search(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", str(title or ""), re.I):
        return "core"
    return "trending"


def mix_status(hist, window=10):
    """hist: newest first, each {status, lane}. Which lane should come next to keep the 50 / 50 mix."""
    kept = [h for h in hist if h.get("status") not in ("cancelled", "failed")][:window]
    c = sum(1 for h in kept if h.get("lane") == "core"); t = len(kept) - c
    if c != t:
        nxt = "core" if c < t else "trending"
    else:
        nxt = "trending" if kept and kept[0].get("lane") == "core" else "core"
    return {"core": c, "trending": t, "window": len(kept), "next": nxt,
            "text": f"last {len(kept)} reels: {c} core, {t} trending; next should be {nxt}"}


def core_bank(prof):
    import json
    return json.dumps([t for t in (prof or {}).get("topic_bank", []) if t.get("lane") in ("core", "academy", None)], ensure_ascii=False, indent=1)

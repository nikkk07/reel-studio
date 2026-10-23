"""Account (brand) profiles: each business has its own knowledge, voice, look, call to action, logo and rules.
Nothing is shared between accounts. We One Aviation keeps its original Hinglish prompts (legacy path, unchanged).
"""
import json, time
from pathlib import Path
from . import config

_CACHE = {}
LEGACY = {"id": "weone", "name": "We One Aviation", "language": "hinglish", "legacy_prompts": True, "instagram": "we_one_aviation"}


def load(db, brand_id):
    brand_id = brand_id or "weone"
    hit = _CACHE.get(brand_id)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    try:
        row = (db.select("brands", f"select=id,name,website,instagram,profile&id=eq.{brand_id}") or [None])[0]
    except Exception:  # noqa: BLE001  (offline DB: legacy only for We One; never guess another account)
        row = None
    if not row:
        if brand_id != "weone":
            raise RuntimeError(f"account '{brand_id}' has no profile")
        p = dict(LEGACY)
    else:
        p = {**(row.get("profile") or {}), "id": row["id"], "name": row["name"], "website": row.get("website")}
        p.setdefault("instagram", row.get("instagram"))
        if row["id"] == "weone":
            p = {**LEGACY, **p, "legacy_prompts": True}
    _CACHE[brand_id] = (time.time(), p)
    return p


def legacy(p):
    return bool(p.get("legacy_prompts")) or p.get("language") == "hinglish"


def logo(p):
    f = p.get("logo_file")
    if f and (config.LIBRARY / f).exists():
        return config.LIBRARY / f
    if p.get("id") not in (None, "weone"):
        raise RuntimeError(f"logo for {p.get('name')} is missing on the Mac ({config.LIBRARY / str(f)})")
    return config.LOGO


def cta(p):
    c = p.get("cta") or {}
    return c.get("say") or f"Follow {p['name']}.", c.get("caption") or f"Follow *{p['name']}*"


def brief(p, full=True):
    """Compact knowledge pack for prompts (only this account's data)."""
    L = [f"# {p['name']} ({p.get('website') or ''})", p.get("business", ""), "",
         f"Tone: {p.get('tone', '')}", f"Niche: {p.get('niche', '')}"]
    for key, title in (("services", "Services"), ("usp", "What makes it different"), ("audience", "Audience"),
                       ("pricing_logic", "How pricing works (explain, never quote a rupee figure)"), ("rules", "Hard rules")):
        if p.get(key):
            L += ["", f"## {title}"] + [f"- {x}" for x in p[key]]
    if p.get("fleet_facts"):
        L += ["", "## Fleet & coverage", p["fleet_facts"]]
    if full:
        if p.get("market_facts"):
            L += ["", "## Dated market facts (cite the source if used)"] + [f"- {m['fact']} [{m['source']}, {m.get('date', '')}]" for m in p["market_facts"]]
        if p.get("competitor_gap"):
            L += ["", "## Positioning vs competitors (never name competitors in a reel)", p["competitor_gap"]]
        if p.get("pillars"):
            L += ["", "## Content pillars"] + [f"- {x['id']}: {x['name']} ({x['why']})" for x in p["pillars"]]
        if p.get("hook_patterns"):
            L += ["", "## Hook patterns that fit this account"] + [f"- {x}" for x in p["hook_patterns"]]
    vs = p.get("visual_style") or {}
    if vs:
        L += ["", "## Visual style", vs.get("look", ""), "Footage ideas: " + ", ".join(vs.get("footage_keywords", [])),
              "Avoid: " + ", ".join(vs.get("avoid", [])), "Graphics: " + vs.get("graphics", "")]
    return "\n".join(x for x in L if x is not None)


def topic_bank(p):
    return json.dumps(p.get("topic_bank") or [], ensure_ascii=False, indent=1)

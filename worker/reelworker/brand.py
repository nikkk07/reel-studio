"""Account (brand) profiles: each business has its own knowledge, voice, look, call to action, logo and rules.
Nothing is shared between accounts. We One Aviation keeps its original Hinglish prompts (legacy path, unchanged).
"""
import hashlib, json, logging, time
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
        row = (db.select("brands", f"select=id,name,website,instagram,logo_asset,profile&id=eq.{brand_id}") or [None])[0]
    except Exception:  # noqa: BLE001  (offline DB: legacy only for We One; never guess another account)
        row = None
    if not row:
        if brand_id != "weone":
            raise RuntimeError(f"account '{brand_id}' has no profile")
        p = dict(LEGACY)
    else:
        p = {**(row.get("profile") or {}), "id": row["id"], "name": row["name"], "website": row.get("website")}
        p.setdefault("instagram", row.get("instagram"))
        p["logo_asset"] = row.get("logo_asset")
        if row["id"] == "weone":
            p = {**LEGACY, **p, "legacy_prompts": True}
    _CACHE[brand_id] = (time.time(), p)
    return p


def legacy(p):
    """We One Aviation's original Hinglish prompts, lexicon and CTA. Every other account (Hinglish or not) uses its own profile."""
    return bool(p.get("legacy_prompts"))


def hinglish(p):
    return p.get("language") == "hinglish"


ASSETS_BUCKET = "brand-assets"


def prepare_logo(src, dst):
    """Any uploaded logo -> a clean PNG for the reel: at least 600 px, and a solid (non-transparent) logo gets rounded corners."""
    from PIL import Image, ImageDraw
    im = Image.open(src); im.load(); im = im.convert("RGBA")
    if max(im.size) < 600:
        k = 600 / max(im.size); im = im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
    if im.getextrema()[3][0] == 255:                       # no transparency: round the corners so it sits like an app tile
        m = Image.new("L", im.size, 0); ImageDraw.Draw(m).rounded_rectangle((0, 0, im.width - 1, im.height - 1), radius=int(min(im.size) * 0.16), fill=255)
        im.putalpha(m)
    dst.parent.mkdir(parents=True, exist_ok=True); tmp = dst.with_suffix(".tmp.png"); im.save(tmp); tmp.replace(dst)
    return dst


def logo(p, db=None):
    """The account's own logo: the one uploaded on the site (Assets), else its file in the library. Never another account's."""
    a = p.get("logo_asset")
    if a and db is not None:
        dst = config.LIBRARY / "brands" / p.get("id", "x") / "uploaded" / (hashlib.sha1(a.encode()).hexdigest()[:16] + ".png")
        if dst.exists():
            return dst
        try:
            raw = dst.with_suffix(".src")
            raw.parent.mkdir(parents=True, exist_ok=True)
            db.download(a, raw, bucket=ASSETS_BUCKET)
            prepare_logo(raw, dst); raw.unlink(missing_ok=True)
            return dst
        except Exception as e:  # noqa: BLE001
            logging.getLogger("reelworker").warning("uploaded logo for %s could not be used (%s); using the stored one", p.get("name"), str(e)[:120])
    f = p.get("logo_file")
    if f and (config.LIBRARY / f).exists():
        return config.LIBRARY / f
    if p.get("id") not in (None, "weone"):
        raise RuntimeError(f"logo for {p.get('name')} is missing on the Mac ({config.LIBRARY / str(f)})")
    return config.LOGO


def cta(p):
    c = p.get("cta") or {}
    if hinglish(p):
        return c.get("say") or f"follow करें {p['name']}।", c.get("caption") or f"follow karein *{p['name']}*"
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
        if p.get("strategy"):
            L += ["", "## Content strategy (from Account Intelligence)", p["strategy"]]
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

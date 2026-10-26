"""Account Intelligence: study one account (website, socials, competitors, market, industry rules) and write its profile.

Runs for every new account right away, for every account once a month, and when someone presses Re-study.
The previous profile is kept in the job output, so a bad study can be undone."""
import json, re, urllib.parse, urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from .. import agents, brain, config, schemas
from .. import brand as brandmod

KEEP = ("name", "website", "instagram", "language", "tts_lang", "asr_lang", "voice", "cta", "end_title", "palette", "logo_file",
        "watermark", "legacy_prompts", "note", "mix")


def check_profile(p):
    probs = []
    bank = p.get("topic_bank", [])
    if len(bank) < 16:
        probs.append(f"topic bank has {len(bank)} topics; it needs at least 16")
    if sum(1 for t in bank if t.get("lane") == "core") < 10:
        probs.append("fewer than 10 core topics in the topic bank")
    if sum(1 for t in bank if str(t.get("source", "")).startswith("http")) < len(bank) * 0.8:
        probs.append("topics without a real source URL")
    if len(p.get("services", [])) < 2 or len(p.get("business", "")) < 80:
        probs.append("the business and its services are not described")
    if len(p.get("rules", [])) < 3:
        probs.append("fewer than 3 hard rules (include the industry's advertising rules)")
    if not any(str(s).startswith("http") for s in p.get("sources", [])):
        probs.append("no sources listed")
    return probs


def _hex(c):
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(c or ""))
    return [int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4)] if m else None


class _Icons(HTMLParser):
    def __init__(self):
        super().__init__(); self.found = []
    def handle_starttag(self, tag, a):
        a = dict(a)
        if tag == "link" and a.get("href") and any(k in (a.get("rel") or "").lower() for k in ("apple-touch-icon", "icon")):
            size = max([int(x) for x in re.findall(r"(\d+)x\d+", a.get("sizes") or "")] or [180 if "apple" in (a.get("rel") or "") else 32])
            self.found.append((size, a["href"]))


def fetch_logo(website, dst):
    """Best effort: the site's largest app icon (apple-touch-icon / icon) as the reel logo until someone uploads one in Assets."""
    req = urllib.request.Request(website, headers={"User-Agent": "Mozilla/5.0 ReelStudio"})
    html = urllib.request.urlopen(req, timeout=20).read(800_000).decode("utf-8", "ignore")
    p = _Icons(); p.feed(html)
    for size, href in sorted(p.found, reverse=True):
        if size < 96:
            continue
        url = urllib.parse.urljoin(website, href)
        raw = dst.with_suffix(".src")
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20).read(5_000_000))
        try:
            brandmod.prepare_logo(raw, dst); return True
        except Exception:  # noqa: BLE001  (an .ico or .svg Pillow can't read)
            continue
        finally:
            raw.unlink(missing_ok=True)
    return False


def text_logo(name, color, dst):
    """Clean placeholder: initials on the brand colour (replaced the moment someone uploads the real logo)."""
    from PIL import Image, ImageDraw, ImageFont
    from ..media import gfx_base
    size = 600; im = Image.new("RGBA", (size, size), tuple((_hex(color) or [10, 35, 66])) + (255,))
    words = [w for w in re.findall(r"[A-Za-z0-9]+", name) if w.lower() not in ("the", "of", "and")]
    ini = "".join(w[0] for w in words[:2]).upper() or name[:2].upper()
    fonts = list(config.FONTS_DIR.rglob("Poppins-ExtraBold.ttf"))
    f = ImageFont.truetype(str(fonts[0]), 260) if fonts else ImageFont.load_default()
    d = ImageDraw.Draw(im); w = d.textlength(ini, font=f)
    d.text(((size - w) / 2, 120), ini, font=f, fill=(255, 255, 255, 255))
    dst.parent.mkdir(parents=True, exist_ok=True); tmp = dst.with_suffix(".tmp.png"); im.save(tmp); tmp.replace(dst)
    brandmod.prepare_logo(dst, dst)


def run(ctx):
    bid = ctx.job.get("brand") or (ctx.job.get("input") or {}).get("brand")
    row = (ctx.db.select("brands", f"select=id,name,website,instagram,links,color,status,profile&id=eq.{bid}") or [None])[0]
    if not row:
        raise RuntimeError(f"account '{bid}' no longer exists")
    old = row.get("profile") or {}
    ctx.progress("research", 0.05); ctx.log(f"Account Intelligence: studying {row['name']}")
    perf = "not connected yet"
    try:
        from .autopilot import history, performance_text
        h = history(ctx.db, brand=bid)
        perf = performance_text(h) if h else "no reels yet"
    except Exception:  # noqa: BLE001
        pass
    current = {k: v for k, v in old.items() if k not in ("logo_file",)}
    if "profile" not in ctx.data:
        p = brain.prompt("account_intel", brand_name=row["name"], website=row.get("website") or "unknown",
                         links=json.dumps({**(row.get("links") or {}), "instagram_handle": row.get("instagram")}, ensure_ascii=False),
                         performance=perf, current=json.dumps(current, ensure_ascii=False, indent=1)[:30000] if current else "(new account: empty)",
                         playbook=brain.prompt("ig_playbook"))
        ctx.data["profile"] = agents.call(ctx, "account_intel", p, schemas.PROFILE, ["WebSearch", "WebFetch"], timeout=2400,
                                          check=check_profile, on_event=lambda m: ctx.log(m), name="account_intel")
        ctx.save()
    new = dict(ctx.data["profile"])
    ctx.progress("research", 0.85)
    for k in KEEP:                             # decisions people made stay as they are
        if k in old:
            new[k] = old[k]
    new["name"], new["website"] = row["name"], row.get("website")
    new.setdefault("instagram", row.get("instagram"))
    lang = new.get("language", "english")
    new.setdefault("tts_lang", "hi" if lang == "hinglish" else "en"); new.setdefault("asr_lang", new["tts_lang"])
    new.setdefault("voice", "female"); new.setdefault("end_title", "FOLLOW FOR MORE")
    sub = " ".join(str(new.get("end_sub") or "").split())
    if len(sub) > 32:                          # end card line: whole words only
        sub = sub[:33].rsplit(" ", 1)[0].rstrip(" ,;:-")
    new["end_sub"] = old.get("end_sub") or sub
    if "palette" not in new and _hex(row.get("color")) and bid != "weone":
        new["palette"] = {"accent": _hex(row.get("color")), "secondary": [233, 236, 240]}
    if bid != "weone":                         # the account's own logo for its reels (an upload in Assets always wins)
        lf = f"brands/{bid}/logo.png"; dst = config.LIBRARY / lf
        if not dst.exists():
            try:
                got = fetch_logo(row.get("website") or "", dst)
            except Exception as e:  # noqa: BLE001
                got = False; ctx.log(f"could not take the logo from the website ({str(e)[:80]}); using a clean placeholder until one is uploaded in Assets", "warn")
            if not got:
                text_logo(row["name"], row.get("color"), dst)
        new["logo_file"] = lf
    new["version"] = int(old.get("version") or 0) + 1
    new["updated"] = datetime.now(timezone.utc).date().isoformat()
    upd = {"profile": new, "intel_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    if row.get("status") == "setup":
        upd["status"] = "live"                 # studied: reels can be made (posting needs its own connected Instagram)
    ctx.db.update("brands", f"id=eq.{bid}", upd)
    brandmod._CACHE.pop(bid, None)
    ctx.finish("done", study={"version": new["version"], "topics": len(new.get("topic_bank", [])), "competitors": [c.get("name") for c in new.get("competitors", [])][:6],
                              "language": lang, "rules": new.get("rules", [])[:6], "strategy": new.get("strategy", "")[:1200], "sources": new.get("sources", [])[:20],
                              "went_live": upd.get("status") == "live"},
               previous_profile=old)
    ctx.log(f"{row['name']} studied: {len(new.get('topic_bank', []))} topics, {len(new.get('competitors', []))} competitors, profile v{new['version']}")

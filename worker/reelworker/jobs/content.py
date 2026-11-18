"""A Meta ad picture ordered in the CRM (Content > Ad creatives) for an approved strategy's image ad. The Ad Creative Designer
writes the few words on the picture from the approved brief; each size (feed 1080x1350, Stories/Reels 1080x1920) is drawn over
a frame of our own 3D clips when one matches, and sent back to the CRM. Nothing is posted: the ad stays paused in Meta."""
import base64, json, re
from .. import agents, brain, config, crm, schemas
from .. import brand as brandmod
from ..media import slides as slidelib

LIMITS = {"headline": 40, "sub": 90, "badge": 24, "cta": 20}
_RISKY = re.compile(r"(\d+\s*%|₹|\brs\.?\s*\d|\blakh|\bguarantee|\bbest\b|\bno\.?\s*1\b|\b#1\b|\btop\s+\d)", re.I)


def check(o, brief_text, banned=()):
    p = [f"{k} over {n} characters" for k, n in LIMITS.items() if len(str(o.get(k, ""))) > n]
    if len(str(o.get("headline", ""))) < 4:
        p.append("write the headline")
    words = " ".join(str(o.get(k, "")) for k in LIMITS).lower()
    for m in _RISKY.finditer(words):
        if m.group(0).lower() not in brief_text.lower():
            p.append(f"\"{m.group(0)}\" is not in the approved brief or the verified facts")
    for b in banned:
        if b and b.lower() in words:
            p.append(f"banned claim used: {b}")
    return p


def run(ctx):
    inp = ctx.job.get("input") or {}
    if inp.get("make") != "ad_image":
        raise RuntimeError(f"unknown content job {inp.get('make')}")
    prof = brandmod.load(ctx.db, ctx.job.get("brand") or "weone")
    brief = inp.get("brief") or {}
    facts = inp.get("brand_facts") or ""
    banned = [str(x) for x in inp.get("banned_claims") or [] if str(x).strip()]
    btxt = json.dumps(brief, ensure_ascii=False) + " " + (facts if isinstance(facts, str) else json.dumps(facts, ensure_ascii=False))
    if "words" not in ctx.data:
        ctx.progress("script", 0.1); ctx.log("Ad Creative Designer: writing the words on the picture")
        p = brain.prompt("ad_picture", brand_name=prof["name"], brief=json.dumps(brief, ensure_ascii=False, indent=1),
                         facts=facts if isinstance(facts, str) else json.dumps(facts, ensure_ascii=False, indent=1),
                         persona=json.dumps(inp.get("persona") or {}, ensure_ascii=False), banned=", ".join(banned) or "none listed")
        ctx.data["words"] = agents.call(ctx, "ad_designer", p, schemas.AD_PICTURE, [], timeout=600,
                                        check=lambda o: check(o, btxt, banned), name="ad_picture")
        ctx.save()
    w = ctx.data["words"]
    bg = None
    try:
        from ..media import library3d
        bg = library3d.frame(w.get("background") or brief.get("visual_brief") or "", ctx.path("ad", "bg.jpg"))
    except Exception:  # noqa: BLE001
        bg = None
    sent = ctx.data.setdefault("sent", [])
    for i, size in enumerate(inp.get("sizes") or ["1080x1350"]):
        if size in sent:
            continue
        ctx.progress("render", 0.3 + 0.6 * i / max(1, len(inp.get("sizes") or [1])))
        f = slidelib.ad_picture(w, size, ctx.path("ad", f"{size}.jpg"), str(config.FONTS_DIR), str(brandmod.logo(prof, ctx.db)), bg)
        r = crm.call("picture", id=int(inp["crm_item"]), size=size, image_b64=base64.b64encode(f.read_bytes()).decode())
        if not (r or {}).get("ok"):
            raise RuntimeError(f"the CRM did not take the {size} picture: {(r or {}).get('detail')}")
        sent.append(size); ctx.save()
        ctx.log(f"ad picture {size} sent to the CRM")
    ctx.finish("done", title=inp.get("title"), ad_words=w, pictures=sent)

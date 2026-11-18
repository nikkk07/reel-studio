"""A CRM-planned Instagram carousel (a reel job with input.format = "carousel"): the Carousel Designer writes and verifies the
slides, they are drawn at 1080x1350, stored like a reel's files, and the job goes to Review. From there it is a reel in every way
that matters: the 5-minute rule, Hold, Approve, Reject; on approval it posts to Instagram at the CRM's planned time."""
import json, re
from .. import agents, brain, config, schemas, instagram
from .. import brand as brandmod
from ..media import slides as slidelib

_BAD = re.compile(r"(https?://|www\.|\b[\w-]+\.(com|in|net|org|io|co)\b|@\w|\b[6-9]\d{9}\b)", re.I)


def check(o, banned=()):
    p = []
    s = o.get("slides") or []
    if not 5 <= len(s) <= 10:
        p.append("give 5 to 10 slides")
    if s and s[0].get("kind") != "cover":
        p.append("slide 1 must be the cover")
    if s and s[-1].get("kind") != "cta":
        p.append("the last slide must be the save/send (cta) slide")
    for i, x in enumerate(s, 1):
        if len(str(x.get("headline", ""))) > 60:
            p.append(f"slide {i}: headline over 60 characters")
        if len(str(x.get("body", ""))) > 240:
            p.append(f"slide {i}: body over 240 characters")
        if _BAD.search(f"{x.get('headline', '')} {x.get('body', '')}"):
            p.append(f"slide {i}: no links, handles or phone numbers on slides")
    text = " ".join(f"{x.get('headline', '')} {x.get('body', '')}" for x in s).lower() + " " + str(o.get("caption", "")).lower()
    for b in banned:
        if b and b.lower() in text:
            p.append(f"banned claim used: {b}")
    if not o.get("claims"):
        p.append("list the verified facts you used in claims, each with its source_url")
    if not 5 <= len(str(o.get("title", ""))) <= 80:
        p.append("title of 5-80 characters")
    return p


def run(ctx):
    inp = ctx.job.get("input") or {}
    concept = inp.get("concept") or {}
    prof = brandmod.load(ctx.db, ctx.job.get("brand") or "weone")
    banned = [str(x) for x in inp.get("banned_claims") or [] if str(x).strip()]
    if inp.get("notes"):
        concept = {**concept, "revision_notes": inp["notes"]}
    if "carousel" not in ctx.data:
        ctx.progress("script", 0.1); ctx.trace("script", "write", "Carousel Designer", "run")
        ctx.log("Carousel Designer: researching and writing the slides")
        p = brain.prompt("carousel", brand_name=prof["name"], concept=json.dumps(concept, ensure_ascii=False, indent=1),
                         brand_brief=brandmod.brief(prof, full=False), banned=", ".join(banned) or "none listed")
        ctx.data["carousel"] = agents.call(ctx, "carousel_designer", p, schemas.CAROUSEL, ["WebSearch", "WebFetch"], timeout=1500,
                                           check=lambda o: check(o, banned), name="carousel", on_event=lambda m: ctx.log(m))
        ctx.save(); ctx.trace("script", "write", "Carousel Designer", "done", flush=True)
    c = ctx.data["carousel"]
    ctx.progress("render", 0.4); ctx.log(f"Drawing {len(c['slides'])} slides")
    handle = "@" + ((prof.get("instagram") or "").lstrip("@")) if prof.get("instagram") else ""
    cover_bg = None
    try:
        from ..media import library3d
        cover_bg = library3d.frame(f"{concept.get('title', '')} {' '.join(concept.get('visual_ideas') or [])}", ctx.path("carousel", "cover_bg.jpg"))
    except Exception:  # noqa: BLE001  (no 3D folder: the brand gradient is used)
        cover_bg = None
    files = slidelib.carousel(c["slides"], ctx.dir / "carousel", str(config.FONTS_DIR), str(brandmod.logo(prof, ctx.db)), handle, cover_bg)
    ctx.progress("publish", 0.7)
    paths = []
    for f in files:
        key = f"{ctx.id}/{f.name}"
        ctx.db.upload(key, f, "image/jpeg"); paths.append(key)
    ctx.db.upload(f"{ctx.id}/thumb.jpg", files[0], "image/jpeg")
    own = (prof.get("instagram") or instagram.OUR).lstrip("@")
    cap = instagram.compose(c["caption"], c["hashtags"], own=own)
    ctx.finish("review", title=c["title"], format="carousel", slides=paths, thumb=f"{ctx.id}/thumb.jpg", ig_caption=cap,
               claims=[{"claim": x.get("claim"), "source_url": x.get("source_url")} for x in c.get("claims", [])])
    ctx.log("carousel ready for review")

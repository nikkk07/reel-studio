from .. import brain, schemas
from .. import brand as brandmod


def recent_titles(db, n=25, brand="weone"):
    try:
        rows = db.select("jobs", f"select=output&kind=eq.reel&brand=eq.{brand}&order=created_at.desc&limit={n}")
        return [r["output"].get("title") for r in rows if (r.get("output") or {}).get("title")]
    except Exception:  # noqa: BLE001
        return []


def run(ctx):
    if (ctx.job.get("input") or {}).get("autopilot"):
        from . import autopilot
        return autopilot.run(ctx)
    ctx.progress("research", 0.1); ctx.log("finding trending, verifiable topics")
    inp = ctx.job.get("input") or {}
    note = inp.get("note", "")
    if inp.get("avoid"):
        note = (note + "\n" if note else "") + "Do not repeat these recent suggestions: " + "; ".join(map(str, inp["avoid"][:10]))
    bid = ctx.job.get("brand") or "weone"; prof = brandmod.load(ctx.db, bid)
    recent = ", ".join(recent_titles(ctx.db, brand=bid)) or "none"
    if brandmod.legacy(prof):
        p = brain.prompt("suggest", recent_titles=recent, user_note=note or "none")
    else:                                   # this account's own knowledge, topic bank and history only
        p = brain.prompt("suggest_brand", brand_name=prof["name"], brand_brief=brandmod.brief(prof), topic_bank=brandmod.topic_bank(prof),
                         recent_titles=recent, user_note=note or "none", language=prof.get("language", "english").title())
    res = brain.ask("suggest", p, schemas.SUGGEST, ["WebSearch", "WebFetch"], ctx.dir / "brain", timeout=900, on_event=lambda m: ctx.log(m))
    ctx.finish("done", topics=res["topics"])
    ctx.log("5 topics ready")

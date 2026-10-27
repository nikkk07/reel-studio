from .. import agents, brain, lanes, schemas
from .. import brand as brandmod
from .autopilot import check_topics, history, library, performance_text


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
    ctx.progress("research", 0.1); ctx.log("Strategist: finding the best topics for this account")
    inp = ctx.job.get("input") or {}
    note = inp.get("note", "")
    if inp.get("avoid"):
        note = (note + "\n" if note else "") + "Do not repeat these recent suggestions: " + "; ".join(map(str, inp["avoid"][:10]))
    bid = ctx.job.get("brand") or "weone"; prof = brandmod.load(ctx.db, bid)
    recent = ", ".join(recent_titles(ctx.db, brand=bid)) or "none"
    try:                                     # this account's own reels only
        hist = history(ctx.db, brand=bid, prof=prof)
        mix = lanes.mix_status(hist)
        perf = performance_text(hist, library(ctx.db, bid, hist))      # what this account's real audience responded to
    except Exception:  # noqa: BLE001
        mix = {"text": "unknown", "next": "core"}; perf = "not available"
    common = dict(recent_titles=recent, user_note=note or "none", lane_core=lanes.rules(prof, "core"), lane_trending=lanes.rules(prof, "trending"),
                  mix=mix["text"], topic_bank=lanes.core_bank(prof), playbook=brain.prompt("ig_playbook"), performance=perf)
    if brandmod.legacy(prof):            # We One: Hinglish hooks, the academy's own knowledge
        p = brain.prompt("suggest", brand_brief=brandmod.brief(prof, full=False), **common)
    else:
        p = brain.prompt("suggest_brand", brand_name=prof["name"], brand_brief=brandmod.brief(prof),
                         language=prof.get("language", "english").title(), **common)
    res = agents.call(ctx, "strategist", p, schemas.SUGGEST, ["WebSearch", "WebFetch"], timeout=900,
                      on_event=lambda m: ctx.log(m), name="suggest", check=check_topics)
    topics = res["topics"]
    for t in topics:                     # every idea carries its lane; the lane the account needs next comes first
        t["lane"] = lanes.of(t.get("lane"), t.get("category"), t.get("title"), prof)
    topics.sort(key=lambda t: t["lane"] != mix.get("next"))
    ctx.finish("done", topics=topics, mix={k: mix.get(k) for k in ("core", "trending", "window", "next")},
               lane_names={k: lanes.name(prof, k) for k in lanes.LANES})
    ctx.log(f"{len(topics)} topics ready")

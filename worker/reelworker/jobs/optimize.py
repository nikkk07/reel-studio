import json
from .. import agents, brain, schemas
from .. import brand as brandmod


def run(ctx):
    inp = ctx.job.get("input") or {}
    text = str(inp.get("prompt", "")).strip()[:2000]
    if not text:
        raise ValueError("empty topic")
    prev = inp.get("previous")
    prev_txt = (json.dumps(prev, ensure_ascii=False)[:3000] + f"\nFeedback: {inp.get('feedback', '')}") if prev else "none"
    ctx.progress("research", 0.1); ctx.log("verifying and sharpening the topic")
    prof = brandmod.load(ctx.db, ctx.job.get("brand") or "weone")
    if brandmod.legacy(prof):
        p = brain.prompt("optimize", user_prompt=text, previous=prev_txt)
    else:
        p = brain.prompt("optimize_brand", user_prompt=text, previous=prev_txt, brand_name=prof["name"], brand_brief=brandmod.brief(prof),
                         language=prof.get("language", "english").title(), cta_say=brandmod.cta(prof)[0])
    res = agents.call(ctx, "strategist", p + "\n\n" + brain.prompt("ig_playbook"), schemas.OPTIMIZE, ["WebSearch", "WebFetch"], timeout=900,
                      on_event=lambda m: ctx.log(m), name="optimize")
    ctx.finish("done", concept=res)
    ctx.log("optimized concept ready")

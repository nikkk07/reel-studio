import json
from .. import brain, schemas


def run(ctx):
    inp = ctx.job.get("input") or {}
    text = str(inp.get("prompt", "")).strip()[:2000]
    if not text:
        raise ValueError("empty topic")
    prev = inp.get("previous")
    prev_txt = (json.dumps(prev, ensure_ascii=False)[:3000] + f"\nFeedback: {inp.get('feedback', '')}") if prev else "none"
    ctx.progress("research", 0.1); ctx.log("verifying and sharpening the topic")
    res = brain.ask("optimize", brain.prompt("optimize", user_prompt=text, previous=prev_txt), schemas.OPTIMIZE,
                    ["WebSearch", "WebFetch"], ctx.dir / "brain", timeout=900, on_event=lambda m: ctx.log(m))
    ctx.finish("done", concept=res)
    ctx.log("optimized concept ready")

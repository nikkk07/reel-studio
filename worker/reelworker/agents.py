"""The agent team. Every Claude decision goes through one specialised agent:

* its role and contract come first in the prompt,
* the lessons it has learned (from Instagram results and from the Resource Manager) are added for its account,
* every call is logged in agent_runs (time, success, grade, lessons used) - the evidence the learning loop grades,
* the Supervisor checks the output against the agent's contract and sends it back once, with the reasons, if it breaks it.
"""
import json, logging, time
from . import brain
from .runner import QuotaError

log = logging.getLogger("reelworker")

ROLES = {
    "account_intel": "Account Intelligence: you study a business from every public source and write the knowledge profile and content strategy all other agents work from. Precise, sourced, never invented.",
    "trend_scout": "Trend Scout: you find what this account's audience is talking about right now, verified on authoritative sources, and estimate viral potential honestly.",
    "strategist": "Strategist: you turn the account's knowledge, its own results and today's trends into Reel topics that grow the account without losing its identity.",
    "viral_editor": "Viral Editor: you independently judge topic candidates for Instagram performance. You did not write them; be strict and calibrated.",
    "fact_checker": "Fact-Checker: you research one topic on official and authoritative sources and hand the writer a verified fact sheet. Nothing unverified gets through you.",
    "scriptwriter": "Scriptwriter: you write short-form video scripts that stop the scroll in the first second and keep people watching to the end, using only verified facts.",
    "hook_critic": "Hook Critic: you are an independent short-form video editor. You score a script before it is produced and say exactly what to fix. You did not write it.",
    "footage_picker": "Footage Picker: you pick the stock clip that shows exactly what each line says, and refuse clips that contradict it.",
    "quality_inspector": "Quality Inspector: you watch the finished reel frame by frame and catch anything a viewer would notice.",
    "pronunciation_editor": "Pronunciation Editor: you rewrite narration into the exact spoken form a voice model pronounces correctly (respellings, letter-by-letter abbreviations, numbers as words), never changing the words or meaning.",
    "voice_inspector": "Voice Inspector: you check recorded narration word by word against what should have been said and reject any take with a mispronounced, wrong or missing word.",
    "caption_writer": "Caption Writer: you write Instagram captions that get searched, saved and sent, in the account's own voice.",
    "analyst": "Performance Analyst: you read the account's real Instagram results and turn them into specific, testable lessons for the other agents.",
    "resource_manager": "Resource Manager: you grade every agent from its run log and write concrete improvements that raise quality and save Claude usage.",
}
MAX_LESSONS = 8
_CACHE = {}


def lessons(db, agent, brand):
    """Active and proven lessons for this agent: this account's own plus the ones for every account (never another account's)."""
    key = (agent, brand)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    try:
        rows = db.select("agent_lessons", f"select=id,lesson,brand,status&agent=eq.{agent}&status=in.(active,proven)"
                                          f"&or=(brand.is.null,brand.eq.{brand})&order=created_at.desc&limit={MAX_LESSONS}")
    except Exception:  # noqa: BLE001
        rows = []
    _CACHE[key] = (time.time(), rows)
    return rows


def preamble(agent, brand_name, learned):
    text = [f"# Your role in Reel Studio's agent team\n{ROLES.get(agent, agent)}\nAccount: {brand_name}. Use only this account's information."]
    if learned:
        text.append("## Lessons you have learned from our own results (apply them unless they conflict with a fact or a hard rule)\n"
                    + "\n".join(f"- {r['lesson']}" for r in learned))
    return "\n\n".join(text) + "\n\n---\n\n"


def record(db, agent, job_id, brand, ok, ms=None, grade=None, note=None, lesson_ids=()):
    try:
        db.insert("agent_runs", {"agent": agent, "job_id": job_id, "brand": brand, "ok": bool(ok), "ms": ms,
                                 "grade": None if grade is None else round(float(grade), 1), "note": (note or "")[:500] or None,
                                 "lesson_ids": list(lesson_ids)})
    except Exception as e:  # noqa: BLE001  (the log is best effort; never fail a reel over it)
        log.warning("agent run log failed: %s", str(e)[:120])


def call(ctx, agent, text, schema, tools, timeout=1200, check=None, on_event=None, grade=None, name=None, cwd=None):
    """One agent decision. check(obj) -> list of contract problems; the Supervisor sends the work back once with them.
    grade(obj) -> 0-100 quality grade logged for the learning loop (optional). name = the call's name (logs, test fixtures)."""
    call_name = name or agent
    cwd = cwd or (ctx.dir / "brain")
    db, brand = ctx.db, ctx.job.get("brand") or "weone"
    from . import brand as brandmod
    try:
        acct = brandmod.load(db, brand).get("name", brand)
    except Exception:  # noqa: BLE001
        acct = brand
    learned = lessons(db, agent, brand)
    ids = [r["id"] for r in learned]
    full = preamble(agent, acct, learned) + text
    t0 = time.time()
    try:
        obj = brain.ask(call_name, full, schema, tools, cwd, timeout=timeout, on_event=on_event)
    except QuotaError:
        raise                                     # not the agent's fault: no failed run is logged
    except Exception as e:
        record(db, agent, ctx.id, brand, False, int((time.time() - t0) * 1000), note=str(e)[:300], lesson_ids=ids)
        raise
    problems = check(obj) if check else []
    if problems:
        record(db, "supervisor", ctx.id, brand, False, note=f"{agent}: " + "; ".join(problems)[:400])
        ctx.log(f"Supervisor sent the {agent.replace('_', ' ')}'s work back: {problems[0][:120]}", "warn")
        fix = full + "\n\n## The Supervisor rejected your previous answer\nFix every problem below and answer again in full:\n" + \
              "\n".join(f"- {p}" for p in problems) + "\n\nYour previous answer:\n" + json.dumps(obj, ensure_ascii=False)[:12000]
        obj = brain.ask(call_name, fix, schema, tools, cwd, timeout=timeout, on_event=on_event)
        problems = check(obj)
        if problems:
            record(db, agent, ctx.id, brand, False, int((time.time() - t0) * 1000), note="contract: " + "; ".join(problems)[:400], lesson_ids=ids)
            raise RuntimeError(f"the {agent.replace('_', ' ')} broke its rules twice: {problems[0][:160]}")
    elif check:
        record(db, "supervisor", ctx.id, brand, True, note=f"{agent}: contract ok")
    g = None
    if grade:
        try:
            g = grade(obj)
        except Exception:  # noqa: BLE001
            g = None
    record(db, agent, ctx.id, brand, True, int((time.time() - t0) * 1000), grade=g, lesson_ids=ids)
    return obj

"""Daily learning run for one account (06:05 India time):

1. evaluate - every active lesson is checked against what happened since it was applied:
              worse than its baseline -> rolled back automatically; clearly better -> proven; too early -> kept.
2. analyst  - reads the account's real Instagram results and writes new lessons (needs at least 3 measured reels)
3. resource manager (once a day, on the first account's run) - grades every agent from the run log and writes improvements

Lessons are applied to the next calls of their agent automatically (agents.lessons); everything is visible on the Agents page.
"""
import json
from datetime import datetime, timedelta, timezone
from statistics import mean, median
from .. import agents, brain, schemas
from .. import brand as brandmod
from .autopilot import history, library, library_text

MIN_MEASURED = 3            # reels with Instagram results before the Analyst may write lessons
MIN_AFTER = 3               # results needed after a lesson before it is judged
ROLLBACK = 0.85             # rolled back if the metric falls below 85 % of its baseline
PROVEN = 1.05               # proven if it is at least 5 % better with 5+ results


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def _measured(hist):
    return [h for h in hist if h.get("eng") is not None and (h.get("hours_live") or 0) >= 24]


def _grades(db, agent, brand, since=None, until=None, ok_only=True):
    q = f"select=grade,ok,created_at,brand&agent=eq.{agent}&order=created_at.desc&limit=200"
    if brand:
        q += f"&brand=eq.{brand}"
    if since:
        q += f"&created_at=gte.{_iso(since)}"
    if until:
        q += f"&created_at=lt.{_iso(until)}"
    return db.select("agent_runs", q)


def metric_value(db, lesson, hist, after=True):
    """(value, n) of the lesson's metric after it was applied (after=True) or for the 10 results before it (baseline)."""
    t0 = _parse(lesson["created_at"]) if lesson.get("created_at") else datetime.now(timezone.utc)
    m, brand = lesson["metric"], lesson.get("brand")
    if m == "engagement":
        pts = [(h["posted_at"], h["eng"]) for h in _measured(hist) if h.get("posted_at")]
        vals = [e for p, e in pts if (_parse(p) >= t0) == after]
        vals = vals if after else vals[:10]
        return (median(vals), len(vals)) if vals else (None, 0)
    agent = {"critic": "hook_critic", "qc": "quality_inspector"}.get(m, lesson["agent"])
    rows = _grades(db, agent, brand, since=t0 if after else None, until=None if after else t0)
    if not after:
        rows = rows[:10]
    if m == "success":
        return (mean(1.0 if r["ok"] else 0.0 for r in rows) * 100, len(rows)) if rows else (None, 0)
    vals = [float(r["grade"]) for r in rows if r.get("grade") is not None]
    return (mean(vals), len(vals)) if vals else (None, 0)


def evaluate(db, brand, hist, log):
    rows = db.select("agent_lessons", f"select=*&status=eq.active&or=(brand.eq.{brand},brand.is.null)")
    changed = []
    for l in rows:
        if l.get("brand") is None and brand != "weone":
            continue                                    # account-wide lessons are judged once, on the first account's run
        val, n = metric_value(db, l, hist, after=True)
        base = l.get("baseline")
        if val is None or n < MIN_AFTER or base is None:
            continue
        base = float(base)
        res = {"after": round(val, 2), "baseline": round(base, 2), "n": n, "at": _iso(datetime.now(timezone.utc))}
        if base > 0 and val < ROLLBACK * base:
            db.update("agent_lessons", f"id=eq.{l['id']}", {"status": "rolled_back", "result": res, "evaluated_at": res["at"]})
            changed.append(f"rolled back #{l['id']} ({l['agent']}): {res['after']} vs {res['baseline']}")
        elif n >= 5 and val >= PROVEN * base:
            db.update("agent_lessons", f"id=eq.{l['id']}", {"status": "proven", "result": res, "evaluated_at": res["at"]})
            changed.append(f"proven #{l['id']} ({l['agent']}): {res['after']} vs {res['baseline']}")
        else:
            db.update("agent_lessons", f"id=eq.{l['id']}", {"result": res, "evaluated_at": res["at"]})
    for c in changed:
        log(c)
    return changed


def add_lessons(db, items, brand, by, hist, default_metric):
    """Insert new lessons with their baseline; keep at most agents.MAX_LESSONS live lessons per agent and account."""
    made = []
    for it in items:
        lesson = {"agent": it["agent"], "brand": brand, "lesson": str(it["lesson"]).strip()[:600], "why": str(it.get("why", ""))[:600],
                  "metric": it.get("metric") if it.get("metric") in ("engagement", "critic", "qc", "success") else default_metric,
                  "created_by": by, "status": "active"}
        if len(lesson["lesson"]) < 10:
            continue
        base, _ = metric_value(db, {**lesson, "created_at": None}, hist, after=False)
        lesson["baseline"] = None if base is None else round(base, 3)
        made.append(db.insert("agent_lessons", lesson)[0])
        live = db.select("agent_lessons", f"select=id,status&agent=eq.{lesson['agent']}&status=in.(active,proven)"
                                          + (f"&brand=eq.{brand}" if brand else "&brand=is.null") + "&order=created_at.desc")
        for old in live[agents.MAX_LESSONS:]:
            if old["status"] == "active":
                db.update("agent_lessons", f"id=eq.{old['id']}", {"status": "retired"})
    agents._CACHE.clear()
    return made


def reels_text(hist):
    keys = ("created", "lane", "category", "title", "hook", "posted", "hours_live", "views", "reach", "shares", "saved", "likes", "comments",
            "avg_watch_s", "skip_rate", "eng", "critic", "qc", "said")
    return "\n".join(json.dumps({k: h.get(k) for k in keys if h.get(k) is not None}, ensure_ascii=False) for h in hist[:40]) or "none"


def lessons_text(db, brand):
    rows = db.select("agent_lessons", f"select=id,agent,lesson,status,metric,baseline,result&status=in.(active,proven)"
                                      + (f"&or=(brand.eq.{brand},brand.is.null)" if brand else "&brand=is.null"))
    return "\n".join(f"#{r['id']} [{r['agent']}, {r['status']}, {r['metric']} baseline {r.get('baseline')} -> {(r.get('result') or {}).get('after')}] {r['lesson']}"
                     for r in rows) or "none"


def team_stats(db, days=14):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.select("agent_runs", f"select=agent,ok,ms,grade,note&created_at=gte.{_iso(since)}&order=created_at.desc&limit=2000")
    by = {}
    for r in rows:
        by.setdefault(r["agent"], []).append(r)
    lines, probs = [], []
    for a, rs in sorted(by.items()):
        g = [float(r["grade"]) for r in rs if r.get("grade") is not None]
        ms = [r["ms"] for r in rs if r.get("ms")]
        lines.append(f"{a}: {len(rs)} runs, {100 * sum(r['ok'] for r in rs) / len(rs):.0f}% ok, avg {mean(ms) / 1000:.0f}s" if ms else f"{a}: {len(rs)} runs, {100 * sum(r['ok'] for r in rs) / len(rs):.0f}% ok")
        if g:
            lines[-1] += f", avg grade {mean(g):.0f}"
        probs += [f"[{a}] {r['note']}" for r in rs if not r["ok"] and r.get("note")][:6]
    return lines, probs, len(rows)


def quality_problems(db, days=14):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.select("jobs", f"select=output&kind=eq.reel&created_at=gte.{_iso(since)}&order=created_at.desc&limit=40")
    out = []
    for r in rows:
        o = r.get("output") or {}
        out += [f"[QC] {i.get('problem', '')[:200]}" for i in (o.get("qc") or {}).get("issues", [])[:3]]
        out += [f"[critic {(o.get('critic') or {}).get('total')}] {(o.get('critic') or {}).get('summary', '')[:200]}"] if o.get("critic") else []
    return out[:30]


def run(ctx):
    inp = ctx.job.get("input") or {}
    bid = ctx.job.get("brand") or inp.get("brand") or "weone"
    prof = brandmod.load(ctx.db, bid)
    ctx.progress("research", 0.05); ctx.log(f"Learning run for {prof['name']}")
    hist = history(ctx.db, brand=bid, prof=prof)
    posts = library(ctx.db, bid, hist)          # the account's other Instagram posts: past content the audience already judged
    past = [p for p in posts if p.get("eng") is not None and (p.get("hours_live") or 0) >= 24]
    report = {"day": inp.get("day"), "reels": len(hist), "measured": len(_measured(hist)), "past_posts": len(posts), "past_measured": len(past)}
    report["evaluated"] = evaluate(ctx.db, bid, hist, ctx.log)

    if report["measured"] + report["past_measured"] >= MIN_MEASURED and "analyst" not in ctx.data:
        ctx.progress("research", 0.3); ctx.log(f"Performance Analyst: reading {report['measured']} measured reels and {len(posts)} other posts of the account")
        res = agents.call(ctx, "analyst", brain.prompt("analyst", brand_name=prof["name"], brand_brief=brandmod.brief(prof, full=False),
                                                       reels=reels_text(hist) + library_text(posts, 15), lessons=lessons_text(ctx.db, bid)),
                          schemas.ANALYST, [], timeout=900, name="analyst")
        ctx.data["analyst"] = res; ctx.save()
        for lid in res.get("retire", []):
            ctx.db.update("agent_lessons", f"id=eq.{int(lid)}&status=eq.active&brand=eq.{bid}", {"status": "retired"})
        made = add_lessons(ctx.db, [{**l, "metric": "engagement"} for l in res.get("lessons", [])], bid, "analyst", hist, "engagement")
        report.update(insights=res.get("insights"), new_lessons=[m.get("lesson") for m in made])
    elif report["measured"] + report["past_measured"] < MIN_MEASURED:
        report["insights"] = (f"Only {report['measured'] + report['past_measured']} posts have 24 h of Instagram results with reach; "
                              f"the Analyst starts writing lessons at {MIN_MEASURED}.")
        ctx.log(report["insights"])

    if inp.get("resources") and "resources" not in ctx.data:
        stats, probs, n = team_stats(ctx.db)
        if n >= 5:
            ctx.progress("research", 0.7); ctx.log(f"Resource Manager: grading the team from {n} agent runs")
            res = agents.call(ctx, "resource_manager", brain.prompt("resource_manager", stats="\n".join(stats), problems="\n".join(probs + quality_problems(ctx.db)) or "none",
                                                                    lessons=lessons_text(ctx.db, None)),
                              schemas.RESOURCES, [], timeout=900, name="resource_manager")
            ctx.data["resources"] = res; ctx.save()
            made = add_lessons(ctx.db, res.get("lessons", []), None, "resource_manager", hist, "critic")
            report.update(health=res.get("health"), usage_note=res.get("usage_note"), team_lessons=[m.get("lesson") for m in made], stats=stats)
        else:
            report["usage_note"] = "Not enough agent runs yet for the Resource Manager."
    ctx.finish("done", learn=report)
    ctx.log("learning run done")

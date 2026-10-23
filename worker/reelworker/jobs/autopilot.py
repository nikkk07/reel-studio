"""Autopilot: one reel a day with no human step.

1. learn  - our own Instagram results (views, reach, sends, saves, watch time; written by the cloud poster)
2. scout  - Claude researches what is trending today and proposes 6 verifiable topics        (WebSearch, WebFetch)
3. filter - code drops repeats of recent reels
4. judge  - a second, independent Claude call scores every topic on a 10-point rubric         (no tools)
5. pick   - code computes the weighted score (judge 75 %, scout 25 %) and picks the winner
6. make   - queues the reel; the review timer posts it, unless the reel's own quality check holds it back
"""
import json, random, re
from datetime import datetime, timedelta, timezone
from statistics import median
from .. import brain, schemas

IST = timezone(timedelta(hours=5, minutes=30))
WEIGHTS = {"hook": .18, "shareability": .16, "curiosity": .12, "saveability": .10, "timeliness": .10,
           "audience_fit": .10, "visual_fit": .08, "verifiability": .07, "novelty": .05, "account_fit": .04}
# posted with nobody watching: never on its own about deaths, attacks or a live tragedy (a person can still choose such a topic)
SENSITIVE = re.compile(r"\b(killed|died|dead|deaths?|death toll|fatal(ities)?|victims?|crash(ed)? (kills|victims)|terror\w*|hijack\w*|bomb\w*|"
                       r"missing (plane|flight|aircraft)|shot down|suicide|mayday crash|black box (found|recovered)|marne?|maut|haadsa)\b", re.I)
STOP = set("the a an of to in on for and or is are was ka ki ke ko se hai hain me mein kya kyu kyun kaise ye yeh wo woh aur bhi jab tak".split())


def _words(t):
    return {w for w in re.findall(r"[a-z0-9]+", str(t or "").lower()) if w not in STOP and len(w) > 2}


def similar(a, b):
    x, y = _words(a), _words(b)
    return len(x & y) / max(1, min(len(x), len(y))) if x and y else 0.0


def engagement(s):
    """Interactions per 100 people reached, sends and saves weighted the way Instagram ranks them."""
    reach = s.get("reach") or s.get("views") or 0
    if not reach:
        return None
    return round(100 * (4 * (s.get("shares") or 0) + 3 * (s.get("saved") or 0) + 2 * (s.get("comments") or 0) + (s.get("likes") or 0)) / reach, 2)


def history(db, days=60):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = db.select("jobs", f"select=id,status,input,output,created_at&kind=eq.reel&created_at=gte.{since}&order=created_at.desc&limit=120")
    out = []
    for r in rows:
        o, i = r.get("output") or {}, r.get("input") or {}
        title = o.get("title") or (i.get("concept") or {}).get("title")
        if not title:
            continue
        st = o.get("ig_stats") or {}
        posted = (o.get("ig") or {}).get("posted_at")
        when = datetime.fromisoformat(posted.replace("Z", "+00:00")).astimezone(IST) if posted else None
        out.append({"title": title, "status": r["status"], "category": (i.get("concept") or {}).get("category") or i.get("category"),
                    "hook": (i.get("concept") or {}).get("hook"), "autopilot": "autopilot" in i,
                    "posted": when.strftime("%a %H:%M") if when else None, "hours_live": st.get("hours_since_post"),
                    "views": st.get("views"), "reach": st.get("reach"), "likes": st.get("likes"), "comments": st.get("comments"),
                    "shares": st.get("shares"), "saved": st.get("saved"), "avg_watch_s": st.get("avg_watch_s"),
                    "skip_rate": st.get("reels_skip_rate"), "eng": engagement(st), "created": r["created_at"][:10]})
    return out


def performance_text(hist):
    rated = [h for h in hist if h["eng"] is not None or h["views"] is not None]
    if not rated:
        posted = [h for h in hist if h["posted"]]
        return (f"{len(posted)} reels posted so far, results not measured yet. Rely on general Instagram Reels evidence "
                "for Indian aviation audiences; give account_fit 5.")
    views = [h["views"] for h in rated if h["views"] is not None]
    lines = [f"{len(rated)} measured reels. Median views {int(median(views)) if views else 'n/a'}. "
             "eng = (4*sends + 3*saves + 2*comments + likes) per 100 reached. Few reels = weak evidence; treat patterns as hints."]
    for h in sorted(rated, key=lambda h: (h["eng"] or 0), reverse=True)[:12]:
        lines.append(f"- [{h['category'] or '?'}] \"{h['title']}\" hook: \"{(h['hook'] or '')[:80]}\" | posted {h['posted']} | "
                     f"{h['hours_live']} h live | views {h['views']} reach {h['reach']} sends {h['shares']} saves {h['saved']} "
                     f"likes {h['likes']} comments {h['comments']} avg watch {h['avg_watch_s']} s skip {h['skip_rate']} | eng {h['eng']}")
    cats = {}
    for h in rated:
        if h["category"] and h["eng"] is not None:
            cats.setdefault(h["category"], []).append(h["eng"])
    if cats:
        lines.append("By category (median eng): " + ", ".join(f"{c} {median(v):.1f} (n={len(v)})" for c, v in sorted(cats.items(), key=lambda x: -median(x[1]))))
    return "\n".join(lines)


def score(topics, judged):
    by = {s["index"]: s for s in judged.get("scores", [])}
    for i, t in enumerate(topics):
        s = by.get(i)
        if not s:
            t["_score"], t["_why"] = None, "not scored"; continue
        rubric = sum(WEIGHTS[c] * max(0.0, min(10.0, float(s.get(c) or 0))) for c in WEIGHTS) * 10          # 0-100
        t["_rubric"] = {c: s.get(c) for c in WEIGHTS}
        t["_score"] = round(0.75 * rubric + 0.25 * max(0, min(100, int(t.get("viral_score") or 50))), 1) if s.get("safe") and not t.get("_sensitive") else None
        t["_why"] = s.get("note", "")
        t["_better"] = {"hook": (s.get("better_hook") or "").strip(), "title": (s.get("better_title") or "").strip()}
    return topics


def run(ctx):
    inp = ctx.job.get("input") or {}
    today = datetime.now(IST).date()
    # a scheduled run the Mac only sees on a later day is skipped (never a surprise reel at night)
    if inp.get("trigger") == "schedule" and inp.get("day") and inp["day"] != today.isoformat():
        ctx.log(f"skipped: the {inp['day']} run was missed because the Mac was offline", "warn")
        return ctx.finish("done", autopilot={"skipped": "missed", "day": inp["day"]}, topics=[])
    if inp.get("trigger") == "schedule":
        st = (ctx.db.select("settings", "select=value&key=eq.autopilot") or [{}])[0].get("value") or {}
        if st.get("on") is False:     # switched off after the cloud queued today's run
            ctx.log("skipped: Autopilot was switched off", "warn")
            return ctx.finish("done", autopilot={"skipped": "off", "day": inp.get("day")}, topics=[])
    made = ctx.db.select("jobs", f"select=id&kind=eq.reel&input->>autopilot=eq.{ctx.id}&limit=1")
    if made:      # crash after queuing: never make a second reel for the same run
        return ctx.finish("done", autopilot={**(ctx.job.get("output") or {}).get("autopilot", {}), "reel_id": made[0]["id"]})

    ctx.progress("research", 0.05); ctx.log("Autopilot: learning from our posted reels")
    hist = history(ctx.db)
    perf = performance_text(hist)
    recent = "\n".join(f"- {h['created']} [{h['category'] or '?'}] {h['title']}" for h in hist[:40]) or "none"
    cats = [h["category"] for h in hist if h["autopilot"] and h["category"]][:7]
    ctx.data["performance"] = perf; ctx.save()

    if "scout" not in ctx.data:
        ctx.progress("research", 0.1); ctx.log("Autopilot: researching what is trending today")
        p = brain.prompt("autopilot_scout", today=today.strftime("%A %d %B %Y"), performance=perf, recent=recent,
                         recent_categories=", ".join(cats) or "none yet")
        ctx.data["scout"] = brain.ask("autopilot_scout", p, schemas.SCOUT, ["WebSearch", "WebFetch"], ctx.dir / "brain",
                                      timeout=1200, on_event=lambda m: ctx.log(m))["topics"]
        ctx.save()
    topics = [dict(t) for t in ctx.data["scout"]]

    titles = [h["title"] for h in hist]
    for t in topics:
        t["_repeat"] = max((similar(t["title"], x) for x in titles), default=0) >= 0.6
        t["_sensitive"] = bool(SENSITIVE.search(" ".join(str(t.get(k) or "") for k in ("title", "hook", "angle", "trend_signal"))))
    fresh = [t for t in topics if not t["_repeat"]] or topics
    if len(fresh) < len(topics):
        ctx.log(f"Autopilot: dropped {len(topics) - len(fresh)} topic(s) too close to recent reels")

    if "judge" not in ctx.data:
        ctx.progress("research", 0.6); ctx.log(f"Autopilot: an independent editor is scoring {len(fresh)} topics")
        order = list(range(len(fresh))); random.Random(ctx.id).shuffle(order)      # no position bias
        shown = [{"index": k, **{f: fresh[i].get(f) for f in ("title", "hook", "angle", "why_viral", "category", "trend_signal", "key_facts", "sources", "risk_note")}}
                 for k, i in enumerate(order)]
        j = brain.ask("autopilot_judge", brain.prompt("autopilot_judge", today=today.strftime("%A %d %B %Y"), performance=perf, recent=recent,
                                                      candidates=json.dumps(shown, ensure_ascii=False, indent=1)),
                      schemas.JUDGE, [], ctx.dir / "brain", timeout=600)
        for s in j.get("scores", []):            # back to fresh[] positions
            s["index"] = order[s["index"]] if 0 <= s.get("index", -1) < len(order) else -1
        ctx.data["judge"] = j; ctx.save()
    score(fresh, ctx.data["judge"])
    ranked = sorted([t for t in fresh if t.get("_score") is not None], key=lambda t: -t["_score"])
    if not ranked:
        raise RuntimeError("the editor judged every topic unsafe; trying again with new research")

    win = ranked[0]
    concept = {k: win.get(k) for k in ("title", "hook", "angle", "why_viral", "key_facts", "sources", "risk_note", "est_seconds", "category")}
    if win.get("_better", {}).get("hook"):
        concept["hook"] = win["_better"]["hook"]
    if win.get("_better", {}).get("title"):
        concept["title"] = win["_better"]["title"]
    concept["est_seconds"] = concept.get("est_seconds") or 35
    ctx.progress("research", 0.9)
    reel = ctx.db.insert("jobs", {"kind": "reel", "input": {"concept": concept, "source": "autopilot", "autopilot": ctx.id,
                                                             "category": win.get("category"), "virality": win["_score"]}})[0]
    pick = {"title": concept["title"], "hook": concept["hook"], "category": win.get("category"), "score": win["_score"],
            "why": win.get("_why"), "trend": win.get("trend_signal"), "reel_id": reel["id"], "day": today.isoformat(),
            "summary": ctx.data["judge"].get("summary", "")}
    board = [{**{k: t.get(k) for k in ("title", "hook", "angle", "why_viral", "key_facts", "sources", "risk_note", "est_seconds", "category", "trend_signal")},
              "score": t.get("_score"), "scout_score": t.get("viral_score"), "rubric": t.get("_rubric"), "editor_note": t.get("_why") if not t.get("_sensitive") else "skipped: sensitive topic", "repeat": t.get("_repeat")}
             for t in sorted(topics, key=lambda t: -(t.get("_score") or -1))]
    ctx.finish("done", autopilot=pick, topics=board)
    ctx.log(f"Autopilot picked \"{concept['title']}\" (score {win['_score']}/100, {win.get('category')}); the reel is queued")

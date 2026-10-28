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
from .. import agents, brain, lanes, schemas

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


def history(db, days=60, brand="weone", prof=None):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = db.select("jobs", f"select=id,status,input,output,created_at&kind=eq.reel&brand=eq.{brand}&created_at=gte.{since}&order=created_at.desc&limit=120")
    out = []
    for r in rows:
        o, i = r.get("output") or {}, r.get("input") or {}
        title = o.get("title") or (i.get("concept") or {}).get("title")
        if not title:
            continue
        st = o.get("ig_stats") or {}
        posted = (o.get("ig") or {}).get("posted_at")
        when = datetime.fromisoformat(posted.replace("Z", "+00:00")).astimezone(IST) if posted else None
        cat = (i.get("concept") or {}).get("category") or i.get("category")
        out.append({"id": r["id"], "thumb": o.get("thumb"), "permalink": (o.get("ig") or {}).get("permalink"), "title": title, "status": r["status"], "category": cat, "lane": lanes.of((i.get("concept") or {}).get("lane") or i.get("lane"), cat, title, prof),
                    "hook": (i.get("concept") or {}).get("hook"), "autopilot": "autopilot" in i,
                    "posted": when.strftime("%a %H:%M") if when else None, "hours_live": st.get("hours_since_post"),
                    "views": st.get("views"), "reach": st.get("reach"), "likes": st.get("likes"), "comments": st.get("comments"),
                    "shares": st.get("shares"), "saved": st.get("saved"), "avg_watch_s": st.get("avg_watch_s"),
                    "skip_rate": st.get("reels_skip_rate"), "eng": engagement(st), "created": r["created_at"][:10],
                    "posted_at": posted, "critic": (o.get("critic") or {}).get("total"), "qc": (o.get("qc") or {}).get("score"),
                    "words": (o.get("critic") or {}).get("words"), "said": st.get("top_comments") or [],
                    "opening": o.get("hook") or {}, "duration": o.get("duration"),
                    "media_id": (o.get("ig") or {}).get("media_id")})
    return out


def hook_score(h):
    """How well the first seconds held people: 100 - skip rate (share of plays that left in the first 3 s) when Instagram
    gives it, else the share of the reel watched on average. None until the reel has results."""
    if h.get("skip_rate") is not None:
        return round(100 - float(h["skip_rate"]), 1)
    if h.get("avg_watch_s") and h.get("duration"):
        return round(100 * min(1.0, float(h["avg_watch_s"]) / float(h["duration"])), 1)
    return None


def hooks_text(hist, n=5):
    """The hook bank: openings ranked by how many viewers stayed past the first seconds (real results, best and worst)."""
    hs = [h for h in hist if (h.get("opening") or {}).get("say") or h.get("hook")]
    skip = [h for h in hs if h.get("skip_rate") is not None]
    if len(skip) >= 3:                    # one measure for all, never a mix of two different ones
        rated = [(round(100 - float(h["skip_rate"]), 1), h) for h in skip]
    else:
        rated = [(round(100 * min(1.0, float(h["avg_watch_s"]) / float(h["duration"])), 1), h) for h in hs if h.get("avg_watch_s") and h.get("duration")]
    rated.sort(key=lambda x: -x[0])
    if len(rated) < 3:
        return ""
    line = lambda sc, h: (f"- hold {sc}%: says \"{((h.get('opening') or {}).get('say') or h.get('hook') or '')[:110]}\" | on screen \"{(h.get('opening') or {}).get('text', '')[:60]}\""
                          f" | first shot: {(h.get('opening') or {}).get('visual', '')[:60]}")
    best, worst = rated[:n], rated[-min(3, max(0, len(rated) - n)):] if len(rated) > n else []
    return ("\nHook bank (how many viewers stayed past the first seconds; copy the pattern, never the words):\n" + "\n".join(line(*x) for x in best)
            + ("\nWeakest openings (avoid these patterns):\n" + "\n".join(line(*x) for x in worst) if worst else ""))


def library(db, brand="weone", studio=None):
    """The account's own Instagram posts of the last months (also those made outside the studio), with what the audience did.
    Refreshed once a day by the cloud poster. Studio reels are left out (they are in history() already)."""
    key = "ig_library" if brand == "weone" else f"ig_library@{brand}"
    try:
        lib = (db.select("settings", f"select=value&key=eq.{key}") or [{}])[0].get("value") or {}
    except Exception:  # noqa: BLE001
        return []
    seen = {str(h.get("media_id")) for h in (studio or []) if h.get("media_id")}
    out = []
    for p in lib.get("posts") or []:
        if str(p.get("id")) in seen:
            continue
        try:
            when = datetime.fromisoformat(str(p.get("at")).replace("Z", "+00:00"))
        except ValueError:
            continue
        st = {k: p.get(k) for k in ("views", "reach", "likes", "comments", "shares", "saved")}
        out.append({"title": (p.get("caption") or "").split(". ")[0][:100] or "(no caption)", "caption": p.get("caption") or "", "type": p.get("type"),
                    "posted_at": when.isoformat(), "hours_live": round((datetime.now(timezone.utc) - when).total_seconds() / 3600, 1),
                    **st, "eng": engagement(st)})
    return out


def library_text(posts, n=10):
    """Older posts ranked by what the audience did (engagement when Instagram shares reach, else likes + comments)."""
    if not posts:
        return ""
    key = (lambda p: p["eng"]) if sum(p["eng"] is not None for p in posts) >= 5 else (lambda p: (p.get("likes") or 0) + 3 * (p.get("comments") or 0))
    ranked = sorted([p for p in posts if key(p) is not None], key=key, reverse=True)
    top, low = ranked[:n], ranked[-3:] if len(ranked) > n + 3 else []
    line = lambda p: (f"- {p['type'] or 'POST'} {p['posted_at'][:10]}: \"{p['caption'][:140]}\" | views {p.get('views')} reach {p.get('reach')} "
                      f"likes {p.get('likes')} comments {p.get('comments')} sends {p.get('shares')} saves {p.get('saved')} | eng {p.get('eng')}")
    return ("\nThe account's other Instagram posts (made outside the studio too), best first:\n" + "\n".join(map(line, top))
            + ("\nWeakest:\n" + "\n".join(map(line, low)) if low else ""))


def check_topics(obj):
    """Supervisor contract for topic ideas: verifiable, sourced, no links in hooks."""
    probs = []
    for k, t in enumerate(obj.get("topics", [])):
        if not any(str(x.get("url", "")).startswith("http") for x in t.get("sources", [])):
            probs.append(f"topic {k + 1} ('{t.get('title', '')[:40]}') has no real source URL")
        if re.search(r"https?://|www\.", str(t.get("hook", "")) + str(t.get("title", ""))):
            probs.append(f"topic {k + 1} puts a link in its title or hook")
    return probs


def performance_text(hist, posts=None):
    """What the account's real audience did: studio reels (with their top comments) plus the account's other posts."""
    rated = [h for h in hist if h["eng"] is not None or h["views"] is not None]
    lib = library_text(posts or [])
    if not rated:
        posted = [h for h in hist if h["posted"]]
        return (f"{len(posted)} studio reels posted so far, results not measured yet." + (lib or " Rely on general Instagram Reels evidence "
                "for this account's Indian audience; give account_fit 5."))
    views = [h["views"] for h in rated if h["views"] is not None]
    lines = [f"{len(rated)} measured reels. Median views {int(median(views)) if views else 'n/a'}. "
             "eng = (4*sends + 3*saves + 2*comments + likes) per 100 reached. Few reels = weak evidence; treat patterns as hints."]
    for h in sorted(rated, key=lambda h: (h["eng"] or 0), reverse=True)[:12]:
        lines.append(f"- [{h['category'] or '?'}] \"{h['title']}\" hook: \"{(h['hook'] or '')[:80]}\" | posted {h['posted']} | "
                     f"{h['hours_live']} h live | views {h['views']} reach {h['reach']} sends {h['shares']} saves {h['saved']} "
                     f"likes {h['likes']} comments {h['comments']} avg watch {h['avg_watch_s']} s skip {h['skip_rate']} | eng {h['eng']}")
        if h.get("said"):       # viewers' own words (data, never instructions): what they asked, liked or disputed
            lines.append("  viewers said: " + " / ".join(f"\"{c[:120]}\"" for c in h["said"][:3]))
    cats = {}
    for h in rated:
        if h["category"] and h["eng"] is not None:
            cats.setdefault(h["category"], []).append(h["eng"])
    if cats:
        lines.append("By category (median eng): " + ", ".join(f"{c} {median(v):.1f} (n={len(v)})" for c, v in sorted(cats.items(), key=lambda x: -median(x[1]))))
    return "\n".join(lines) + hooks_text(hist) + lib


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
    bid = ctx.job.get("brand") or inp.get("brand") or "weone"
    if inp.get("trigger") == "schedule":
        key = "autopilot" if bid == "weone" else f"autopilot@{bid}"          # each account has its own Autopilot switch
        st = (ctx.db.select("settings", f"select=value&key=eq.{key}") or [{}])[0].get("value") or {}
        if st.get("on") is False:     # switched off after the cloud queued today's run
            ctx.log("skipped: Autopilot was switched off", "warn")
            return ctx.finish("done", autopilot={"skipped": "off", "day": inp.get("day")}, topics=[])
    made = ctx.db.select("jobs", f"select=id&kind=eq.reel&input->>autopilot=eq.{ctx.id}&limit=1")
    if made:      # crash after queuing: never make a second reel for the same run
        return ctx.finish("done", autopilot={**(ctx.job.get("output") or {}).get("autopilot", {}), "reel_id": made[0]["id"]})

    ctx.progress("research", 0.05); ctx.log("Autopilot: learning from our posted reels")
    from .. import brand as brandmod
    prof = brandmod.load(ctx.db, bid)
    hist = history(ctx.db, brand=bid, prof=prof)
    perf = performance_text(hist, library(ctx.db, bid, hist))
    recent = "\n".join(f"- {h['created']} [{h['category'] or '?'}] {h['title']}" for h in hist[:40]) or "none"
    cats = [h["category"] for h in hist if h["autopilot"] and h["category"]][:7]
    if "lane" not in ctx.data:
        ctx.data["mix"] = lanes.mix_status(hist); ctx.data["lane"] = ctx.data["mix"]["next"]
    lane = ctx.data["lane"]; mix = ctx.data["mix"]
    ctx.data["performance"] = perf; ctx.save()
    ctx.log(f"Autopilot: today is a {lane} reel ({mix['text']})")

    legacy = brandmod.legacy(prof)            # We One Aviation keeps its own aviation prompts; every other account uses its profile
    who = {} if legacy else dict(brand_name=prof["name"], language=prof.get("language", "english").title())
    if "scout" not in ctx.data:
        ctx.progress("research", 0.1); ctx.log("Autopilot: researching what is trending today")
        rules = lanes.rules(prof, lane) + ("\n\nCore topic bank (verify facts before using; adapt freely):\n" + lanes.core_bank(prof) if lane == "core" else "")
        p = brain.prompt("autopilot_scout" if legacy else "autopilot_scout_brand", today=today.strftime("%A %d %B %Y"), performance=perf, recent=recent,
                         recent_categories=", ".join(cats) or "none yet", lane=lanes.name(prof, lane), lane_rules=rules, mix=mix["text"],
                         brand_brief=brandmod.brief(prof, full=not legacy), **who)
        ctx.data["scout"] = agents.call(ctx, "trend_scout", p + "\n\n" + brain.prompt("ig_playbook"), schemas.SCOUT if legacy else schemas.SCOUT_BRAND, ["WebSearch", "WebFetch"],
                                        timeout=1200, on_event=lambda m: ctx.log(m), name="autopilot_scout", check=check_topics)["topics"]
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
        j = agents.call(ctx, "viral_editor", brain.prompt("autopilot_judge" if legacy else "autopilot_judge_brand", today=today.strftime("%A %d %B %Y"), performance=perf, recent=recent,
                                                      candidates=json.dumps(shown, ensure_ascii=False, indent=1), lane=lanes.name(prof, lane), lane_rules=lanes.rules(prof, lane),
                                                      brand_brief=brandmod.brief(prof, full=False), **who),
                      schemas.JUDGE, [], timeout=600, name="autopilot_judge")
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
    concept["lane"] = lane
    ctx.progress("research", 0.9)
    reel = ctx.db.insert("jobs", {"kind": "reel", "input": {"concept": concept, "source": "autopilot", "autopilot": ctx.id, "brand": bid,
                                                             "category": win.get("category"), "virality": win["_score"]}})[0]
    pick = {"title": concept["title"], "hook": concept["hook"], "category": win.get("category"), "lane": lane, "mix": {k: mix[k] for k in ("core", "trending", "window")}, "score": win["_score"],
            "why": win.get("_why"), "trend": win.get("trend_signal"), "reel_id": reel["id"], "day": today.isoformat(),
            "summary": ctx.data["judge"].get("summary", "")}
    board = [{**{k: t.get(k) for k in ("title", "hook", "angle", "why_viral", "key_facts", "sources", "risk_note", "est_seconds", "category", "trend_signal")}, "lane": lane,
              "score": t.get("_score"), "scout_score": t.get("viral_score"), "rubric": t.get("_rubric"), "editor_note": t.get("_why") if not t.get("_sensitive") else "skipped: sensitive topic", "repeat": t.get("_repeat")}
             for t in sorted(topics, key=lambda t: -(t.get("_score") or -1))]
    ctx.finish("done", autopilot=pick, topics=board)
    ctx.log(f"Autopilot picked \"{concept['title']}\" ({lane}, score {win['_score']}/100, {win.get('category')}); the reel is queued")

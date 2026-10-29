"""Comment-to-DM call to action. The cloud sends the private replies (ig-poster); the Mac writes the call to action into
the reel ("Comment MEDICAL ...") and the caption, and only while the account's Comment-to-DM is switched on AND the cloud
has confirmed it can send replies (state 'ready'). Otherwise the account's normal call to action is used, so a reel never
promises a message nobody will send.

Every reel gets its own keyword and DM (the CTA Strategist, from the reel's verified facts): the keyword names the topic,
the DM delivers what the reel promised (its key points, the right page, one next step), so commenting is worth it and the
DM earns the follow. The account's own keyword stays as the fallback for older posts and for reels without a plan."""
import re, time
from datetime import datetime

# words people type anyway, or that read as bait ("comment YES"): never a reel keyword
GENERIC = {"YES", "NO", "OK", "OKAY", "NICE", "WOW", "GOOD", "GREAT", "COOL", "LOVE", "INFO", "LINK", "DM", "SEND", "PLEASE", "PLS",
           "HI", "HELLO", "THANKS", "AMAZING", "TRUE", "FACT", "FACTS", "SAME", "BRO", "SIR", "MAAM", "FIRST", "DONE", "WANT", "ME"}


def _key(brand_id):
    return "dm" if brand_id == "weone" else f"dm@{brand_id}"


def account(db, brand_id):
    """The account's Comment-to-DM settings, or None when it is off or not working yet."""
    try:
        rows = db.select("settings", f"select=value&key=eq.{_key(brand_id)}")
        d = (rows[0]["value"] if rows else {}) or {}
    except Exception:  # noqa: BLE001
        return None
    kw = str((d.get("keywords") or [""])[0]).strip().upper()
    return d if d.get("on") and d.get("state") == "ready" and kw else None


def active(db, brand_id, prof, plan=None):
    """The call to action for this reel: its own keyword and offer when it has a plan, else the account's keyword."""
    d = account(db, brand_id)
    if not d:
        return None
    hi = prof.get("language") == "hinglish"
    if plan and plan.get("keyword"):
        kw, offer, say_offer = plan["keyword"], plan["offer"], plan.get("say_offer") or plan["offer"]
        if hi:
            say, cap = f"कमेंट करें {kw}, और हम आपको {say_offer} DM कर देंगे।", f"Comment *{kw}*: {offer[:34]}"
        else:
            say, cap = f"Comment {kw}, and we'll DM you {say_offer}.", f"Comment *{kw}* for {offer[:34]}"
        return {"keyword": kw, "say": say, "caption": cap, "caption_line": f"💬 Comment {kw} and we'll DM you {offer}.", "plan": True}
    kw = str(d["keywords"][0]).strip().upper()
    if hi:
        say, cap = f"कमेंट करें {kw}, और हम आपको पूरी डीटेल्स मैसेज करेंगे।", f"Comment *{kw}*: details DM mein"
    else:
        say, cap = f"Comment {kw}, and we'll message you the details.", f"Comment *{kw}* for details"
    return {"keyword": kw, "say": say, "caption": cap, "caption_line": f"💬 Comment {kw} and we'll DM you the details."}


def is_dm_cta(p, kw):
    """A call-to-action line the writer added itself ('comment PILOT ...'), replaced by the standard one."""
    t = (p.get("say", "") + " " + p.get("caption", "")).lower()
    return kw.lower() in t and ("comment" in t or "कमेंट" in t)


def site_of(url):
    m = re.match(r"https?://(?:www\.)?([^/:?#]+)", str(url or "").strip().lower())
    return m.group(1) if m else ""


UTM = "utm_source=instagram&utm_medium=dm&utm_campaign="


def tag(url, campaign):
    """The DM's website link with UTM tags, so the account's own analytics show visits from each reel's DM (reel_<keyword>)."""
    u = str(url or "").strip()
    if not u or "utm_" in u:
        return u
    frag = ""
    if "#" in u:
        u, frag = u.split("#", 1); frag = "#" + frag
    c = re.sub(r"[^a-z0-9_-]", "", str(campaign).lower())[:40] or "reel"
    return f"{u}{'&' if '?' in u else '?'}{UTM}{c}{frag}"


def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:  # noqa: BLE001
        return None


def results(db, brand_id, days=60, now=None, settle_h=48):
    """How each earlier reel's own keyword did: DMs sent per 1,000 people reached (Instagram reach), measured once the reel
    is at least 2 days old. Newest first. [] when nothing is measurable yet or the data cannot be read."""
    now = now or time.time()
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - days * 86400))
    try:
        rows = db.select("jobs", "select=kw:output->dm->>keyword,offer:output->dm->>offer,mid:output->ig->>media_id,at:output->ig->>posted_at,"
                         f"reach:output->ig_stats->>reach&kind=eq.reel&brand=eq.{brand_id}&output->ig->>state=eq.posted"
                         f"&output->ig->>posted_at=gte.{since}&order=created_at.desc&limit=60")
        rows = [r for r in rows or [] if r.get("kw") and r.get("mid")]
        logs = db.select("dm_log", f"select=media_id,keyword&brand=eq.{brand_id}&state=eq.sent&created_at=gte.{since}&limit=5000") if rows else []
    except Exception:  # noqa: BLE001
        return []
    sent = {}
    for x in logs or []:
        k = (str(x.get("media_id")), str(x.get("keyword") or "").upper()); sent[k] = sent.get(k, 0) + 1
    out = []
    for r in rows:
        at = _ts(r.get("at"))
        if not at or now - at < settle_h * 3600:
            continue
        try:
            reach = int(float(r.get("reach"))) if r.get("reach") not in (None, "") else None
        except (TypeError, ValueError):
            reach = None
        dms = sent.get((str(r["mid"]), str(r["kw"]).upper()), 0)
        out.append({"keyword": str(r["kw"]).upper(), "offer": r.get("offer") or "", "dms": dms, "reach": reach,
                    "per_1k": round(dms * 1000 / reach, 1) if reach else None})
    return out


def history_text(res):
    """The results as lines for the CTA Strategist: best first, so it repeats what earned DMs and drops what did not."""
    if not res:
        return "(no measured results yet: this account's reel keywords are new; follow the rules above)"
    ranked = sorted(res, key=lambda x: (x["per_1k"] is None, -(x["per_1k"] or 0), -x["dms"]))
    lines = []
    for x in ranked[:8]:
        rate = f"{x['per_1k']} per 1,000 reached" if x["per_1k"] is not None else "reach not measured"
        reach = f" from {x['reach']:,} reached" if x["reach"] else ""
        lines.append(f"- {x['keyword']} (offer: {x['offer'] or 'n/a'}): {x['dms']} DMs{reach} = {rate}")
    return "\n".join(lines)


def check_plan(plan, prof, facts_urls, used=()):
    """Contract problems of a DM plan (the Supervisor sends it back once with these)."""
    out = []
    kw = str(plan.get("keyword", "")).strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9]{2,11}", kw):
        out.append("keyword must be ONE word of 3-12 capital letters/numbers, starting with a letter (no spaces, no #)")
    elif kw in GENERIC:
        out.append(f"keyword {kw} is too generic (people type it anyway / reads as engagement bait); use a word that names this reel's topic")
    elif kw in {u.upper() for u in used}:
        out.append(f"keyword {kw} was used by a recent reel of this account; pick a different topic word")
    if not (8 <= len(str(plan.get("offer", ""))) <= 60):
        out.append("offer must be 8-60 characters and name the concrete thing they get (e.g. 'the 5-step medical checklist')")
    pts = [str(x).strip() for x in plan.get("points") or []]
    if not 3 <= len(pts) <= 5 or any(len(x) < 15 or len(x) > 170 for x in pts):
        out.append("points: 3-5 useful points, each 15-170 characters")
    home = site_of(prof.get("website"))
    if home and site_of(plan.get("link")) != home:
        out.append(f"link must be a page on {home} (from the list given)")
    src = plan.get("source")
    if src and site_of(src) not in {site_of(u) for u in facts_urls}:
        out.append("source must be one of the fact sheet's source URLs, or empty")
    if len(str(plan.get("next_step", ""))) > 160:
        out.append("next_step must be one sentence of at most 160 characters")
    if len(str(plan.get("reply", ""))) > 90:
        out.append("reply must be at most 90 characters")
    return out


def message(plan, prof):
    """The DM itself: formal, scannable, value first, then the page, one next step and the follow ask. {name} = @username."""
    name = prof["name"]
    lines = ["Hi {name}, thank you for your comment.", "", f"Here is {plan['offer']}:"]
    lines += [f"{i}. {str(p).strip()}" if str(p).strip().endswith("…") else f"{i}. {str(p).strip().rstrip('.')}." for i, p in enumerate(plan["points"][:5], 1)]
    lines += ["", f"Read more: {tag(plan['link'], 'reel_' + str(plan.get('keyword', '')))}"]
    if plan.get("source"):
        lines.append(f"Official source: {plan['source']}")
    if plan.get("next_step"):
        lines += ["", str(plan["next_step"]).strip()]
    lines += ["", f"Follow {name} for more reels like this.", f"Team {name}"]
    biz = (str(prof.get("business", "")) + " " + str(prof.get("niche", ""))).lower()
    if "legal" in biz or "law" in biz or "advocate" in biz:
        lines.append("This is general information, not legal advice.")
    text = "\n".join(lines)
    while len(text) > 950 and len(plan["points"]) > 3:          # Instagram message limit, with room for the username
        plan = {**plan, "points": plan["points"][:-1]}
        return message(plan, prof)
    if len(text) > 950 and plan.get("source"):                   # then the optional source line, so the end is never cut
        return message({**plan, "source": ""}, prof)
    if len(text) > 950:                                          # last: shorten the longest point at a word boundary
        pts = [str(x) for x in plan["points"]]; i = max(range(len(pts)), key=lambda k: len(pts[k]))
        cut = len(pts[i]) - (len(text) - 950) - 2
        if cut >= 40:
            pts[i] = pts[i][:cut].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…" if " " in pts[i][:cut] else pts[i][:cut] + "…"
            return message({**plan, "points": pts}, prof)
    return text[:950]

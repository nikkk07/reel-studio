"""Comment-to-DM call to action. The cloud sends the private replies (ig-poster); the Mac writes the call to action into
the reel ("Comment MEDICAL ...") and the caption, and only while the account's Comment-to-DM is switched on AND the cloud
has confirmed it can send replies (state 'ready'). Otherwise the account's normal call to action is used, so a reel never
promises a message nobody will send.

Every reel gets its own keyword and DM (the CTA Strategist, from the reel's verified facts): the keyword names the topic,
the DM delivers what the reel promised (its key points, the right page, one next step), so commenting is worth it and the
DM earns the follow. The account's own keyword stays as the fallback for older posts and for reels without a plan."""
import re

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
    if len(str(plan.get("reply", ""))) > 90:
        out.append("reply must be at most 90 characters")
    return out


def message(plan, prof):
    """The DM itself: formal, scannable, value first, then the page, one next step and the follow ask. {name} = @username."""
    name = prof["name"]
    lines = ["Hi {name}, thank you for your comment.", "", f"Here is {plan['offer']}:"]
    lines += [f"{i}. {str(p).strip().rstrip('.')}." for i, p in enumerate(plan["points"][:5], 1)]
    lines += ["", f"Read more: {plan['link']}"]
    if plan.get("source"):
        lines.append(f"Official source: {plan['source']}")
    if plan.get("next_step"):
        lines += ["", str(plan["next_step"]).strip()]
    lines += ["", f"Follow {name} for one useful reel like this every day.", f"Team {name}"]
    biz = (str(prof.get("business", "")) + " " + str(prof.get("niche", ""))).lower()
    if "legal" in biz or "law" in biz or "advocate" in biz:
        lines.append("This is general information, not legal advice.")
    text = "\n".join(lines)
    while len(text) > 950 and len(plan["points"]) > 3:          # Instagram message limit, with room for the username
        plan = {**plan, "points": plan["points"][:-1]}
        return message(plan, prof)
    return text[:950]

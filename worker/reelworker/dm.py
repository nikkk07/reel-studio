"""Comment-to-DM call to action. The cloud sends the private replies (ig-poster); the Mac only writes the call to action
into the reel ("Comment PILOT ...") and the caption, and only while the account's Comment-to-DM is switched on AND the
cloud has confirmed it can send replies (state 'ready'). Otherwise the account's normal call to action is used, so a
reel never promises a message nobody will send."""


def _key(brand_id):
    return "dm" if brand_id == "weone" else f"dm@{brand_id}"


def active(db, brand_id, prof):
    try:
        rows = db.select("settings", f"select=value&key=eq.{_key(brand_id)}")
        d = (rows[0]["value"] if rows else {}) or {}
    except Exception:  # noqa: BLE001
        return None
    kw = str((d.get("keywords") or [""])[0]).strip().upper()
    if not d.get("on") or d.get("state") != "ready" or not kw:
        return None
    if prof.get("language") == "hinglish":
        say, cap = f"कमेंट करें {kw}, और हम आपको पूरी डीटेल्स मैसेज करेंगे।", f"Comment *{kw}*: details DM mein"
        line = f"💬 Comment {kw} and we'll DM you the details."
    else:
        say, cap = f"Comment {kw}, and we'll message you the details.", f"Comment *{kw}* for details"
        line = f"💬 Comment {kw} and we'll DM you the details."
    return {"keyword": kw, "say": say, "caption": cap, "caption_line": line}


def is_dm_cta(p, kw):
    """A call-to-action line the writer added itself ('comment PILOT ...'), replaced by the standard one."""
    t = (p.get("say", "") + " " + p.get("caption", "")).lower()
    return kw.lower() in t and ("comment" in t or "कमेंट" in t)

"""Anchor mode (opt-in, spec["anchor"]): ONE presenter is the main character on screen almost the whole time, the place behind
her changes with each idea (spec["scenes"]), stock footage only appears as short full-screen cutaways, and every caption and
graphic is laid out around her face. Pure timing/layout logic (no drawing); media/anchor_render.py draws the frames.

spec["anchor"] = {"clip": "<HeyGen mp4>", "map": [{"vo_t0", "vo_t1", "clip_t0"}, ...]}   reel seconds -> presenter clip seconds
spec["scenes"] = [{"from_t", "to_t", "bg": {"query": ...} | {"image": ...}, "pos": "center|left|right", "scale": 1.0, "label"}]
spec["captions"] = "kinetic"                                                             word-by-word captions
A spec without these fields never reaches this module (timeline.build only calls enrich() when one of them is present).
"""
import math, re

W, H = 1080, 1920
CUT_MAX = 2.0            # owner rule: stock footage is very short and keeps changing (one clip at most ~2 s)
CUT_TARGET = 1.7         # a longer cutaway is split into pieces of about this length, each a different clip
PLATE_MAX = 3.0          # a moving background plate behind her rotates to the next clip every 2-3 s
PLATE_TARGET = 2.5
WINDOW_WARN = 4.0        # one cutaway window should stay within ~4 s (the presenter is the main character)
MERGE_GAP = 0.3          # a gap shorter than this between presenter windows is no cutaway: she simply continues
TRANS = 0.25             # whip / zoom-blur transition length
CLOSE = 1.22             # punch-in close-up on a key word (a hard cut, like a second camera), zoomed about her face
PUNCH_MIN = 0.8          # a punch never leaves a framing shorter than this
FACE_DEFAULT = (0.22, 0.17, 0.78, 0.48)       # x0, y0, x1, y1 as fractions of the presenter frame (upper middle, hair included)
POS_DX = {"center": 0.0, "left": -0.25, "right": 0.25}
SAFE = (70, 250, 1010, 1560)                  # clear of Instagram's top bar, side buttons and bottom description
CAP_Y = 1380                                  # caption baseline (lower third)
CAP_UP, CAP_DOWN = 110, 50                    # caption box above / below its baseline (full safe width, incl. pop + shadow)
NEW_GFX = ("kinetic_words", "strike", "pill_row", "arrow_cta", "stamp")
CENTER = {"big_text", "counter", "list_card", "quote_card", "compare", "plane_path"}
MOVABLE = ("chip", "ui_card", "scene_label")
TIMER_BOX = (W - 340, 195, W - 60, 327)


# ------------------------------------------------------------------ small helpers
def _r(x):
    return round(float(x), 3)


def inter(a, b):
    """Do two boxes (x0, y0, x1, y1) overlap?"""
    return a is not None and b is not None and a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def union(a, b):
    if a is None: return b
    if b is None: return a
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def norm_word(w):
    return re.sub(r"[^\w]+", "", str(w).replace("*", "")).lower()


def needed(spec):
    """True when the spec asks for anything this module provides."""
    return bool(spec.get("anchor") or spec.get("scenes") or spec.get("captions") == "kinetic"
                or any(isinstance(g, dict) and g.get("type") in NEW_GFX for g in spec.get("graphics") or []))


# ------------------------------------------------------------------ word timings
def syllables(w):
    w = norm_word(w)
    if not w:
        return 1
    if re.search(r"[ऀ-ॿ]", w):                     # Devanagari: roughly one syllable per consonant cluster
        return max(1, len(re.findall(r"[क-हअ-औ]", w)))
    n = len(re.findall(r"[aeiouy]+", w))
    if w.endswith("e") and n > 1 and not w.endswith(("le", "ee")):
        n -= 1
    return max(1, n + (1 if re.search(r"\d", w) else 0) * len(re.findall(r"\d", w)))


def weight(w):
    """How long a word takes to say, relative to the others (syllables plus a little for length)."""
    return 0.6 * syllables(w) + 0.06 * len(norm_word(w)) + 0.25


def caption_words(cap):
    """Caption text -> [(word, highlighted)] (a *starred* run marks the keyword)."""
    out = []
    for m in re.finditer(r"\*([^*]+)\*([.,?!:]*)|(\S+)", str(cap)):
        if m.group(1):
            parts = m.group(1).split()
            for i, p in enumerate(parts):
                out.append((p + (m.group(2) if i == len(parts) - 1 else ""), True))
        else:
            out.append((m.group(3).replace("*", ""), False))
    return [(w, h) for w, h in out if w.strip()]


def _warp(a, b, asr):
    """Fraction of the phrase (by spoken weight) -> time. With recogniser word times inside the phrase the speech rate is
    followed (fast and slow stretches); without them the phrase is spread evenly."""
    pts = [(0.0, a)]
    if asr and len(asr) >= 2:
        tot = sum(max(1, len(w)) for _, _, w in asr); acc = 0
        for s, e, w in asr:
            f0 = acc / tot; acc += max(1, len(w)); f1 = acc / tot
            pts += [(f0, max(a, min(b, s))), (f1, max(a, min(b, e)))]
    pts.append((1.0, b))
    pts.sort()
    xs, ys = [], []
    for x, y in pts:                                     # strictly increasing fractions, never decreasing times
        if xs and x <= xs[-1]:
            ys[-1] = max(ys[-1], y); continue
        xs.append(x); ys.append(max(y, ys[-1]) if ys else y)

    def f(x):
        for i in range(1, len(xs)):
            if x <= xs[i]:
                u = (x - xs[i - 1]) / max(1e-9, xs[i] - xs[i - 1]); return ys[i - 1] + u * (ys[i] - ys[i - 1])
        return ys[-1]
    return f


def word_times(TL, spec, vo=None):
    """Every caption word with its time on the reel clock: [{"w", "t0", "t1", "hl", "g", "p"}]. The voice stage aligns
    whole phrases; inside a phrase each word gets a share proportional to its syllables, following the recogniser's own word
    times when voice_out.json has them (the caption is English-heavy Hinglish, the recognised words are Devanagari, so only
    the speech-rate curve is borrowed, never the words)."""
    out = []
    vo = vo or {}
    for g, sg in zip(spec.get("groups", []), TL["groups"]):
        v = vo.get(g["id"]) or {}
        asr_all = []
        for x in v.get("words") or []:
            try:
                s, e, w = float(x[0]), float(x[1]), str(x[2])
            except Exception:  # noqa: BLE001
                continue
            asr_all.append((sg["t0"] + s - sg["trim"][0], sg["t0"] + e - sg["trim"][0], w))
        for k, p in enumerate(g.get("phrases", [])[:len(sg["spans"])]):
            a, b = sg["spans"][k]
            ws = caption_words(p.get("caption", ""))
            if not ws:
                continue
            asr = [x for x in asr_all if a - 0.05 <= x[0] < b + 0.05]
            f = _warp(a, b, asr)
            wt = [weight(w) for w, _ in ws]; tot = sum(wt); acc = 0.0
            for (w, hl), x in zip(ws, wt):
                t0 = f(acc / tot); acc += x; t1 = f(acc / tot)
                out.append({"w": w, "t0": _r(t0), "t1": _r(max(t1, t0 + 0.04)), "hl": hl, "g": g["id"], "p": k})
    return out


def chunks(words, vo_end, max_words=4, max_chars=22):
    """Kinetic caption chunks: 2-4 words on screen at a time, a new chunk after punctuation, never a lonely last word."""
    out, cur = [], []

    def flush():
        if cur:
            out.append({"words": list(cur), "t0": cur[0]["t0"], "t1": cur[-1]["t1"]}); cur.clear()
    for i, w in enumerate(words):
        if cur and (cur[-1]["g"], cur[-1]["p"]) != (w["g"], w["p"]):
            flush()
        cur.append(w)
        rest = [x for x in words[i + 1:] if (x["g"], x["p"]) == (w["g"], w["p"])]
        chars = sum(len(x["w"]) + 1 for x in cur)
        full = len(cur) >= max_words or chars >= max_chars or (len(cur) >= 2 and re.search(r"[,.?!:।]$", w["w"]))
        if full and len(rest) != 1:
            flush()
        elif full and len(rest) == 1 and len(cur) >= max_words:       # 4 words + 1 left: split 3 + 2
            last = cur.pop(); flush(); cur.append(last)
    flush()
    for i, c in enumerate(out):                                      # on screen until the next chunk (or a short hold)
        nxt = out[i + 1]["t0"] if i + 1 < len(out) else vo_end
        c["show0"] = _r(c["t0"] - 0.06)
        c["show1"] = _r(max(c["t1"], min(c["t1"] + 0.3, nxt - 0.02, vo_end - 0.1)))
        c["y"] = CAP_Y
    return out


# ------------------------------------------------------------------ graphics timing (reveals synced to the voice)
def _match_times(words, items, t0, t1):
    """Time of the first caption word matching each item (in order, inside [t0, t1]), or None."""
    out, j = [], 0
    pool = [w for w in words if t0 - 0.2 <= w["t0"] < t1 + 0.05]
    for it in items:
        key = norm_word(str(it).split()[0]) if str(it).split() else ""
        hit = None
        for k in range(j, len(pool)):
            if key and norm_word(pool[k]["w"]) == key:
                hit = pool[k]["t0"]; j = k + 1; break
        out.append(hit)
    return out


def reveals(items, g, words):
    """Pop-in time of each item: on its spoken word when it is said, else spaced evenly through the graphic."""
    n = len(items)
    if not n:
        return []
    hit = _match_times(words, items, g["t0"], g["t1"])
    gap = min(0.5, max(0.18, (g["t1"] - g["t0"] - 0.5) / max(1, n)))
    out, last = [], g["t0"] + 0.05 - gap
    for i in range(n):
        t = hit[i] - 0.04 if hit[i] is not None else last + gap
        t = max(t, last + 0.12, g["t0"])
        out.append(_r(t)); last = t
    return out


def time_graphics(TL, words):
    gs = TL["graphics"]
    for g in gs:
        p = g.get("params") or {}
        if g["type"] == "kinetic_words":
            ws = [str(x) for x in (p.get("words") or [])][:5]
            g["params"] = {**p, "words": ws}; g["reveal"] = reveals(ws, g, words); g["strikes"] = []
        elif g["type"] == "pill_row":
            its = [str(x) for x in (p.get("items") or [])][:5]
            g["params"] = {**p, "items": its}; g["reveal"] = reveals(its, g, words)
        elif g["type"] == "stamp":
            g["t1"] = _r(max(g["t1"], g["t0"] + 1.4))
    solo = sorted([g for g in gs if g["type"] in NEW_GFX and g["type"] != "strike"], key=lambda g: g["t0"])
    for a, b in zip(solo, solo[1:]):                      # one kinetic graphic at a time: the next one ends the previous
        if a["t1"] > b["t0"]:
            a["t1"] = _r(max(a["t0"] + 0.6, b["t0"] + 0.15))
    for g in gs:                                          # a strike belongs to the kinetic words on screen when it lands
        if g["type"] != "strike":
            continue
        p = g.get("params") or {}
        kws = [(i, k) for i, k in enumerate(gs) if k["type"] == "kinetic_words" and k["t0"] - 0.05 <= g["t0"] + 0.12 < k["t1"] + 0.3]
        if not kws:
            g["orphan"] = True; continue
        ki, kw = kws[-1]; ws = kw["params"]["words"]
        idx = next((i for i, w in enumerate(ws) if norm_word(w) == norm_word(p.get("word", ""))), None)
        if idx is None:
            try: idx = max(0, min(len(ws) - 1, int(p.get("index", 0))))
            except Exception: idx = 0  # noqa: BLE001,E701
        ts = g["t0"] + 0.12
        if p.get("at_word"):
            m = _match_times(words, [p["at_word"]], g["t0"] - 0.5, kw["t1"])[0]
            ts = m if m is not None else ts
        ts = max(ts, kw["reveal"][idx] + 0.25) if idx < len(kw["reveal"]) else ts
        g["t0"] = _r(ts); g["t1"] = kw["t1"]; g["kw"] = ki; g["index"] = idx
        kw["strikes"].append([idx, _r(ts)])


# ------------------------------------------------------------------ anchor timeline
def windows(anchor):
    """spec["anchor"]["map"] -> sorted, non-overlapping presenter windows."""
    out = []
    for m in (anchor or {}).get("map") or []:
        try:
            a, b, c = float(m["vo_t0"]), float(m["vo_t1"]), float(m.get("clip_t0", 0))
        except Exception:  # noqa: BLE001
            continue
        if b - a >= 0.05:
            out.append({"t0": max(0.0, a), "t1": b, "vo_t0": a, "clip_t0": max(0.0, c)})
    out.sort(key=lambda x: x["t0"])
    clean = []
    for w in out:
        if clean and w["t0"] < clean[-1]["t1"]:
            w["t0"] = clean[-1]["t1"]
            if w["t1"] - w["t0"] < 0.05:
                continue
        clean.append(w)
    return clean


def _phrase_at(TL):
    gi = {g["id"]: g for g in TL["groups"]}

    def at(group, phrase):
        g = gi.get(group) or TL["groups"][0]
        n = len(g["spans"]); k = max(0, min(int(phrase or 0), n - 1))
        return g["spans"][k][0]
    return at


def scenes_of(spec, end, TL=None):
    """Scenes on the reel clock. A scene starts at from_t, or at a phrase ("at_group"/"at_phrase", so it follows the
    recorded voice); it ends at to_t, or where the next scene starts."""
    out = []
    at = _phrase_at(TL) if TL and TL.get("groups") else None
    for i, s in enumerate(spec.get("scenes") or []):
        if not isinstance(s, dict):
            continue
        try:
            if at and s.get("at_group") is not None and "from_t" not in s:
                a = max(0.0, at(s["at_group"], s.get("at_phrase", 0)) - float(s.get("lead", 0.08)))
                a = 0.0 if a < 0.3 else a
            else:
                a = max(0.0, float(s["from_t"]))
            b = min(end, float(s["to_t"])) if s.get("to_t") is not None else end
        except Exception:  # noqa: BLE001
            continue
        if b - a < 0.3:
            continue
        bg = s.get("bg") if isinstance(s.get("bg"), dict) else {}
        pos = s.get("pos") if s.get("pos") in POS_DX else "center"
        try: sc = min(1.6, max(0.6, float(s.get("scale") or 1.0)))
        except Exception: sc = 1.0  # noqa: BLE001,E701
        out.append({"i": len(out), "t0": a, "t1": b, "pos": pos, "scale": sc, "label": str(s.get("label") or "")[:28],
                    "bg": {"query": str(bg["query"])[:120]} if str(bg.get("query") or "").strip() else
                          {"image": str(bg["image"])} if bg.get("image") else {}})
    out.sort(key=lambda x: x["t0"])
    for a, b in zip(out, out[1:]):                       # a later scene starts where it says; the earlier one ends there
        a["t1"] = min(a["t1"], b["t0"])
    out = [s for s in out if s["t1"] - s["t0"] >= 0.3]
    for i, s in enumerate(out):
        s["i"] = i; s["t0"], s["t1"] = _r(s["t0"]), _r(s["t1"])
        if s["bg"].get("query"):                          # moving plates rotate every 2-3 s, each a different clip
            d = s["t1"] - s["t0"]; n = max(1, math.ceil(d / PLATE_TARGET - 1e-9)); step = d / n
            s["plates"] = [{"t0": _r(s["t0"] + j * step), "t1": _r(s["t0"] + (j + 1) * step), "src": f"BG{i + 1}", "var": j} for j in range(n)]
    return out


def split(t0, t1, target, mx):
    d = t1 - t0; n = max(1, math.ceil(d / target - 1e-9))
    while d / n > mx:
        n += 1
    return [(t0 + j * d / n, t0 + (j + 1) * d / n) for j in range(n)]


def build(TL, spec):
    """Presenter windows + short cutaway pieces covering the whole reel, scenes, transitions. Replaces TL["shots"]."""
    A = spec["anchor"]; end, vo_end = TL["end"], TL["vo_end"]; warn = []
    wins = windows(A)
    if not wins:
        warn.append("anchor map is empty: the reel is all cutaways")
    if wins and wins[0]["t0"] < MERGE_GAP:
        wins[0]["t0"] = 0.0
    for a, b in zip(wins, wins[1:]):
        if b["t0"] - a["t1"] < MERGE_GAP:
            a["t1"] = b["t0"]
    end_pres = bool(wins) and wins[-1]["t1"] >= vo_end - MERGE_GAP
    if end_pres:
        wins[-1]["t1"] = end                              # she stays through the end card
    gaps, t = [], 0.0
    for w in wins:
        if w["t0"] - t > 1e-6:
            gaps.append([t, w["t0"]])
        t = w["t1"]
    if end - t > 1e-6:
        gaps.append([t, end])
    at = _phrase_at(TL)
    shots = [s for s in spec.get("shots", []) if not s.get("presenter")]
    cuts = [s for s in shots if s.get("cutaway")]
    words = TL.get("words") or []

    def start_of(s):                                      # at its phrase, or on a spoken word ("at_word") inside the phrase
        t = at(s.get("at_group"), s.get("at_phrase", 0))
        if s.get("at_word"):
            m = _match_times(words, [s["at_word"]], t, t + 8.0)[0]
            if m is not None:
                return max(t, m - 0.06)
        return t
    when = {s["id"]: start_of(s) for s in shots}
    in_gap = lambda s, g: g[0] - 0.2 <= when[s["id"]] < g[1] - 0.3  # noqa: E731
    used = {s["id"] for s in cuts if any(in_gap(s, g) for g in gaps)}
    spare = [s for s in cuts if s["id"] not in used]
    var = {}; segs = []
    for g in gaps:
        if g[1] - g[0] > WINDOW_WARN + 1e-6 and g[0] < vo_end:
            warn.append(f"cutaway window {g[0]:.2f}-{g[1]:.2f} s is longer than {WINDOW_WARN:.0f} s")
        mine = sorted([s for s in cuts if in_gap(s, g)], key=lambda s: when[s["id"]])
        if not mine and spare:
            mine = [spare.pop(0)]
        if not mine:
            mine = sorted([s for s in shots if not s.get("cutaway") and in_gap(s, g)], key=lambda s: when[s["id"]])
        if not mine and cuts:
            mine = [cuts[len(segs) % len(cuts)]]
        if not mine and shots:
            mine = [shots[0]]
        if not mine:                                      # nothing at all to cut to: the presenter keeps going
            warn.append(f"no cutaway clip for {g[0]:.2f}-{g[1]:.2f} s; the presenter continues")
            prev = segs[-1] if segs and segs[-1]["kind"] == "anchor" else None
            wins.append({"t0": g[0], "t1": g[1], "vo_t0": prev["vo_t0"] if prev else g[0], "clip_t0": prev["clip_t0"] if prev else 0.0, "fill": True})
            continue
        bounds = [g[0]] + [max(g[0], when[s["id"]] - 0.08) for s in mine[1:]] + [g[1]]
        for s, a, b in zip(mine, bounds, bounds[1:]):
            if b - a < 0.05:
                continue
            for x0, x1 in split(a, b, CUT_TARGET, CUT_MAX):
                v = var.get(s["id"], 0); var[s["id"]] = v + 1
                segs.append({"kind": "cut", "src": s["id"], "var": v, "t0": x0, "t1": x1})
    for w in wins:
        segs.append({"kind": "anchor", "t0": w["t0"], "t1": w["t1"], "vo_t0": w["vo_t0"], "clip_t0": w["clip_t0"], **({"fill": True} if w.get("fill") else {})})
    for p in A.get("punch") or []:                        # camera work: cut wide <-> close on a key word inside her window
        if not isinstance(p, dict) or p.get("at_group") is None:
            continue
        tp = start_of(p)
        for k, s in enumerate(segs):
            if s["kind"] == "anchor" and s["t0"] + PUNCH_MIN <= tp <= s["t1"] - PUNCH_MIN:
                b = {**s, "t0": tp, "close": not s.get("close")}
                s["t1"] = tp; segs.insert(k + 1, b); break
    segs.sort(key=lambda s: s["t0"])
    na = nc = 0
    for i, s in enumerate(segs):
        prev = segs[i - 1] if i else None
        if s["kind"] == "anchor":
            na += 1; s["id"] = f"A{na}"; s["presenter"] = True
            punch = prev is not None and prev["kind"] == "anchor"           # jump cut: punch in instead of a transition
            if s.get("close"):
                s["z0"], s["z1"] = CLOSE, CLOSE + 0.025
            elif "close" in s:                                              # back to the wide framing after a close-up
                s["z0"], s["z1"] = 1.0, 1.03
            else:
                s["z0"], s["z1"] = ((1.10, 1.13) if punch and not prev.get("punch") else (1.0, 1.04))
            s["punch"] = s["z0"] > 1.05
        else:
            nc += 1; s["id"] = f"C{nc}"
            s["z0"], s["z1"] = ((1.0, 1.12) if nc % 2 else (1.16, 1.02))
            s["ramp"] = nc % 2 == 0                        # speed-ramped zoom on every other piece
        s["t0"], s["t1"] = _r(s["t0"]), _r(s["t1"])
        s["need"] = _r(s["t1"] - s["t0"] + 0.3)
    scenes = scenes_of(spec, end, TL)
    TL["shots"] = segs
    need = {}
    for s in segs:
        if s["kind"] == "cut":
            need.setdefault(s["src"], {}); need[s["src"]][s["var"]] = max(need[s["src"]].get(s["var"], 0), s["need"])
    for sc in scenes:
        for p in sc.get("plates", []):
            need.setdefault(p["src"], {})[p["var"]] = _r(p["t1"] - p["t0"] + 0.3)
    TL["anchor"] = {"clip": str(A.get("clip") or ""), "matte": None, "scenes": scenes, "faces": {}, "end_presenter": end_pres,
                    "warnings": warn, "need": {k: {str(v): d for v, d in x.items()} for k, x in need.items()}}
    TL["transitions"] = transitions(TL)
    for sc in scenes:                                     # the scene's optional place tag
        if sc["label"]:
            TL["graphics"].append({"type": "scene_label", "t0": _r(sc["t0"] + 0.2), "t1": _r(min(sc["t1"], sc["t0"] + 2.6)),
                                   "params": {"text": sc["label"], "hold": 2.2}})
    return TL


def scene_at(TL, t):
    for s in (TL.get("anchor") or {}).get("scenes", []):
        if s["t0"] <= t < s["t1"]:
            return s
    return None


def seg_at(TL, t):
    return next((s for s in TL["shots"][::-1] if t >= s["t0"]), TL["shots"][0]) if TL["shots"] else None


def transitions(TL):
    """A 0.25 s whip-pan or zoom-blur at every change of picture: presenter <-> cutaway, a new scene behind her, and the
    first cut into each cutaway window. Pieces inside one cutaway window are quick straight cuts (speed-ramped zooms)."""
    pts = []
    segs = TL["shots"]
    for a, b in zip(segs, segs[1:]):
        if a["kind"] != b["kind"]:
            pts.append(b["t0"])
        elif a["kind"] == "anchor":
            sa, sb = scene_at(TL, b["t0"] - 0.01), scene_at(TL, b["t0"] + 0.01)
            if (sa or {}).get("i") != (sb or {}).get("i"):
                pts.append(b["t0"])
    for sc in TL["anchor"]["scenes"]:
        for edge in (sc["t0"], sc["t1"]):
            s = seg_at(TL, edge + 0.001)
            if s and s["kind"] == "anchor" and edge > 0.2:
                other = scene_at(TL, edge - 0.01) if edge == sc["t0"] else scene_at(TL, edge + 0.01)
                if (other or {}).get("i") != sc["i"]:
                    pts.append(edge)
    out = []
    for t in sorted(set(round(x, 3) for x in pts)):
        if 0.2 <= t <= TL["vo_end"] and (not out or t - out[-1]["t"] > 0.3):
            out.append({"t": t, "kind": "whip" if len(out) % 2 == 0 else "zoom", "d": TRANS})
    return out


def bind(TL, files, durations, log=lambda m, lvl="info": None):
    """Give every cutaway piece and background plate its prepared clip. files = {src: [variant clip, ...]},
    durations = {clip: seconds}. Owner rule: a clip never freezes. A piece longer than its clip is split and the rest cut to
    the next different clip; a piece is never longer than CUT_MAX (plates PLATE_MAX). A source with no clip borrows another
    source's clips; with no clip at all the presenter continues."""
    pool = [f for src in sorted(files) if not src.startswith("BG") for f in files[src]]

    def fill(items, mx, srcfiles):
        out = []
        for it in items:
            cand = list(srcfiles(it["src"]))
            if not cand:
                out.append({**it, "file": None}); continue
            k = it["var"] % len(cand); t = it["t0"]; tries = 0; last = out[-1].get("file") if out else None
            while it["t1"] - t > 1e-3 and tries < 4 * len(cand) + 4:
                f = cand[k % len(cand)]; k += 1; tries += 1
                if f == last and len(cand) > 1:
                    continue
                avail = float(durations.get(f) or 0) - 0.05
                if avail < 0.25:
                    continue
                e = min(it["t1"], t + avail, t + mx)
                if it["t1"] - e < 0.25 and e < it["t1"]:      # never leave a sliver: stop a little earlier instead
                    e = max(t + 0.25, it["t1"] - 0.25) if it["t1"] - t > 0.5 else e
                out.append({**it, "t0": _r(t), "t1": _r(e), "file": f, "off": 0.0}); t = e; last = f
            if it["t1"] - t > 1e-3:
                out.append({**it, "t0": _r(t), "t1": it["t1"], "file": None})
        return out
    segs, fixed = [], 0
    cut_items = [s for s in TL["shots"] if s["kind"] == "cut"]
    bound = fill(cut_items, CUT_MAX, lambda src: files.get(src) or pool)
    fixed = len(bound) - len(cut_items)
    for s in TL["shots"]:
        if s["kind"] == "anchor":
            segs.append(s)
    for b in bound:
        if b["file"] is None:                             # nothing at all: she continues (the clip plays on, never frozen)
            prev = max([x for x in segs if x["kind"] == "anchor" and x["t0"] <= b["t0"]], key=lambda x: x["t0"], default=None)
            segs.append({"kind": "anchor", "t0": b["t0"], "t1": b["t1"], "vo_t0": prev["vo_t0"] if prev else b["t0"],
                         "clip_t0": prev["clip_t0"] if prev else 0.0, "presenter": True, "fill": True, "z0": 1.0, "z1": 1.04})
            log(f"no stock clip for {b['t0']:.2f}-{b['t1']:.2f} s; the presenter continues there", "warn")
        else:
            segs.append(b)
    segs.sort(key=lambda s: s["t0"])
    na = nc = 0
    for s in segs:
        if s["kind"] == "anchor":
            na += 1; s["id"] = f"A{na}"
        else:
            nc += 1; s["id"] = f"C{nc}"
    TL["shots"] = segs
    for sc in TL["anchor"]["scenes"]:
        if sc.get("plates"):
            pl = fill(sc["plates"], PLATE_MAX, lambda src: files.get(src) or [])
            sc["plates"] = [p for p in pl if p["file"]]
            if not sc["plates"]:
                log(f"scene {sc['i'] + 1}: no background clip was found; she keeps her own background there", "warn")
    if fixed:
        log(f"{fixed} cutaway piece(s) re-cut so no clip runs past its end", "info")
    return TL


# ------------------------------------------------------------------ face box and layout
def presenter_geom(TL, s, t):
    """(scale, dx) of the presenter cut-out at time t: the scene's size/position when she is cut out (mask), else as shot."""
    sc = scene_at(TL, t) if TL["anchor"].get("matte") else None
    if not sc:
        return 1.0, 0.0
    return sc["scale"], POS_DX[sc["pos"]] * W


def zoom_at(s, t):
    k = max(0.0, min(1.0, (t - s["t0"]) / max(0.01, s["t1"] - s["t0"])))
    if s.get("ramp"):
        k = 1 - 2 ** (-8 * k)
    return s["z0"] + (s["z1"] - s["z0"]) * k


def zoom_center(TL, s):
    """Point the frame zooms about: her face for a close-up (so the punch-in frames her face), else the frame centre."""
    if not s or not s.get("close"):
        return W / 2, H / 2
    f = TL["anchor"].get("faces", {}).get(s["id"]) or TL["anchor"].get("face") or FACE_DEFAULT
    return (f[0] + f[2]) / 2 * W, (f[1] + f[3]) / 2 * H


def end_shift(TL, t):
    e = TL["anchor"].get("end") or {}
    ta = TL["vo_end"] - 0.1
    if not e or t < ta:
        return 0.0
    k = max(0.0, min(1.0, (t - ta) / 0.6)); k = 1 if k == 1 else 1 - 2 ** (-10 * k)
    return e["dx"] * k


def face_raw(TL, s, t, shift=True):
    """Her face box on the 1080x1920 canvas at time t (None when she is not on screen)."""
    if not s or s["kind"] != "anchor":
        return None
    f = TL["anchor"].get("faces", {}).get(s["id"]) or TL["anchor"].get("face") or FACE_DEFAULT
    sc, dx = presenter_geom(TL, s, t)
    x0, y0, x1, y1 = f[0] * W, f[1] * H, f[2] * W, f[3] * H
    pts = [((x - W / 2) * sc + W / 2 + dx, (y - H) * sc + H) for x, y in ((x0, y0), (x1, y1))]
    z = zoom_at(s, t); cx, cy = zoom_center(TL, s)
    (a, b), (c, d) = [((x - cx) * z + cx, (y - cy) * z + cy) for x, y in pts]
    sh = end_shift(TL, t) if shift else 0.0
    m = 0.03 * W
    return (a - m + sh, b - m, c + m + sh, d + m)


def face_at(TL, t, shift=True):
    return face_raw(TL, seg_at(TL, t), t, shift)


def face_union(TL, t0, t1, step=1 / 30):
    t0, t1 = max(0.0, t0), min(TL["end"] - 1e-3, t1)
    box, t = None, t0
    while t <= t1 + 1e-9:
        box = union(box, face_at(TL, t)); t += step
    for tr in TL.get("transitions") or []:              # a transition can briefly zoom the frame
        if t0 - 0.2 <= tr["t"] <= t1 + 0.2:
            for dt in (-0.1, 0.1):
                if not t0 <= tr["t"] + dt <= t1:
                    continue
                f = face_at(TL, tr["t"] + dt)
                if f and tr["kind"] == "zoom":
                    cx, cy = W / 2, H / 2; z = 1.12
                    f = ((f[0] - cx) * z + cx, (f[1] - cy) * z + cy, (f[2] - cx) * z + cx, (f[3] - cy) * z + cy)
                box = union(box, f)
    return box


def caption_box(y):
    return (SAFE[0], y - CAP_UP, SAFE[2], y + CAP_DOWN)


def _cap_y(F):
    if F is None:
        return CAP_Y
    for y in [CAP_Y] + [int(F[3] + CAP_UP + 30 + 40 * i) for i in range(8)]:
        if y + CAP_DOWN <= SAFE[3] + 40 and not inter(caption_box(y), F):
            return y
    y = int(F[1] - CAP_DOWN - 20)
    return y if y - CAP_UP >= SAFE[1] else None


def classic_box(dy=0):
    return (SAFE[0], 1360 - CAP_UP - 10 + dy, SAFE[2], 1452 + CAP_DOWN + dy)


def classic_dy(F):
    if F is None or not inter(classic_box(), F):
        return 0
    for dy in [int(F[3] - classic_box()[1] + 20 + 30 * i) for i in range(6)]:
        if classic_box(dy)[3] <= SAFE[3] + 40 and not inter(classic_box(dy), F):
            return dy
    dy = int(F[1] - 20 - classic_box()[3])
    return dy if classic_box(dy)[1] >= SAFE[1] else None


DEFAULT_BOX = {"kinetic_words": (90, 520, 990, 1160), "pill_row": (70, 1060, 1010, 1240), "stamp": (90, 640, 990, 1080),
               "arrow_cta": (140, 1080, 940, 1300), "strike": (90, 520, 990, 1160)}
MIN_H = {"kinetic_words": 300, "pill_row": 120, "stamp": 200, "arrow_cta": 160}


def regions(F, cap_top, side=None):
    """Free areas around her face: (name, box) candidates, best first for the given preferred side."""
    out = []
    y0, y1 = SAFE[1] + 60, cap_top - 30
    r = (F[2] + 40, y0, SAFE[2], y1); l = (SAFE[0], y0, F[0] - 40, y1)
    below = (SAFE[0], F[3] + 40, SAFE[2], cap_top - 30); above = (SAFE[0], SAFE[1] + 40, SAFE[2], F[1] - 40)
    sides = [("right", r), ("left", l)]
    sides.sort(key=lambda x: -(x[1][2] - x[1][0]))
    if side in ("left", "right"):
        sides.sort(key=lambda x: x[0] != side)
    out += [x for x in sides if x[1][2] - x[1][0] >= 360]
    out += [("below", below), ("above", above)]
    return [(n, b) for n, b in out if b[2] - b[0] >= 300 and b[3] - b[1] >= 100]


def layout(TL):
    """Move every caption and graphic out of her face. Captions go to the lower third below her chin (or just above her head
    when she fills the frame); graphics to the free side when she stands left/right, else between chin and captions."""
    anchor = TL.get("anchor")
    fa = (lambda a, b: face_union(TL, a, b)) if anchor else (lambda a, b: None)
    for x in (TL.get("kcaps") or []) + TL["captions"] + TL["graphics"]:      # layout can run again (after binding clips / faces)
        x.pop("hide", None); x.pop("dy", None)
        if x.get("orphan"):
            x["hide"] = True
    for c in TL.get("kcaps") or []:
        y = _cap_y(fa(c["show0"], c["show1"]))
        if y is None:
            c["hide"] = True
        else:
            c["y"] = y
    if anchor and not TL.get("kcaps"):                     # classic captions (1-2 lines at 1360-1452): moved as a block
        for c in TL["captions"]:
            F = fa(c["t0"] - 0.04, c["t1"] + 0.15)
            dy = classic_dy(F)
            if dy is None:
                c["hide"] = True
            elif dy:
                c["dy"] = dy
    for g in TL["graphics"]:
        ty = g["type"]
        if ty == "timer" and inter(TIMER_BOX, fa(g["t0"] - 0.05, g["t1"] + 1.0)):
            g["hide"] = True
        if ty not in NEW_GFX and ty not in MOVABLE:
            continue
        if ty == "strike":
            continue
        if anchor and ty in NEW_GFX:                           # a pop-up mostly over a cutaway lives on the cutaway only
            g["t0"], g["t1"] = g.get("t0_full", g["t0"]), g.get("t1_full", g["t1"])
            cuts = [(max(g["t0"], s["t0"]), min(g["t1"], s["t1"])) for s in TL["shots"]
                    if s["kind"] == "cut" and s["t1"] > g["t0"] and s["t0"] < g["t1"]]
            on_cut = sum(b - a for a, b in cuts)
            if cuts and on_cut >= 0.6 and on_cut >= 0.6 * (g["t1"] - g["t0"]):
                g["t0_full"], g["t1_full"] = g["t0"], g["t1"]
                g["t0"], g["t1"] = _r(min(a for a, _ in cuts) + 0.04), _r(max(b for _, b in cuts) - 0.04)
        F = None if g.get("t0_full") is not None else fa(g["t0"] - 0.05, g["t1"] - 0.01)   # faded out by its t1
        if F is None:
            if ty in NEW_GFX:
                g["box"] = list(DEFAULT_BOX[ty])
            continue
        caps = [c["y"] for c in (TL.get("kcaps") or []) if not c.get("hide") and c["show0"] < g["t1"] and c["show1"] > g["t0"]]
        caps += [classic_box(c.get("dy", 0))[1] + CAP_UP for c in TL["captions"]
                 if not TL.get("kcaps") and not c.get("hide") and c["t0"] < g["t1"] and c["t1"] > g["t0"]]
        cap_top = min(caps) - CAP_UP if caps else SAFE[3]
        side = (g.get("params") or {}).get("side")
        cand = regions(F, cap_top, side)
        if ty in ("stamp", "pill_row", "arrow_cta"):            # wide graphics prefer the band below her chin
            cand.sort(key=lambda x: (x[0] not in ("below",) or x[1][3] - x[1][1] < MIN_H.get(ty, 120)))
        cand = [x for x in cand if x[1][3] - x[1][1] >= (MIN_H.get(ty, 120) if ty in NEW_GFX else 110)] or cand
        if not cand:
            g["hide"] = True; continue
        name, box = cand[0]
        if ty in NEW_GFX:
            if name in ("left", "right") and ty == "kinetic_words":     # a column beside her, centred on her face height
                h = min(box[3] - box[1], 620); cy = max(box[1] + h / 2, min(box[3] - h / 2, (F[1] + F[3]) / 2 + 60))
                box = (box[0], cy - h / 2, box[2], cy + h / 2)
            g["box"] = [round(v, 1) for v in box]; g["region"] = name
        else:                                                # old graphics: same look, moved below her chin
            p = dict(g.get("params") or {})
            if name not in ("below", "above"):
                below = next((b for n, b in cand if n in ("below", "above")), None)
                if below is None:
                    g["hide"] = True; continue
                box = below
            if ty == "chip":
                p["y"] = (box[1] + box[3]) / 2
                if box[3] - box[1] < 80: g["hide"] = True  # noqa: E701
            elif ty == "ui_card":
                p["y"] = (box[1] + 20) / H
                if box[3] - box[1] < 210: g["hide"] = True  # noqa: E701
            elif ty == "scene_label":
                p["y"] = (box[1] + 75) / H; p.pop("side", None)
            g["params"] = p
    for g in TL["graphics"]:                                  # the strike follows its words
        if g["type"] == "strike" and not g.get("hide"):
            kw = TL["graphics"][g["kw"]]
            g["box"] = kw.get("box") or list(DEFAULT_BOX["kinetic_words"])
            g["kwg"] = {k: kw.get(k) for k in ("params", "box", "reveal", "t0", "t1")}
            g["kwg"]["box"] = g["box"]
            if kw.get("hide"):
                g["hide"] = True
    if anchor:                                                # end card: she moves aside, the panel takes the other side
        ta = TL["vo_end"] - 0.1
        F = face_raw(TL, seg_at(TL, ta + 0.05), ta + 0.05, shift=False) if anchor.get("end_presenter") else None
        if F is not None:
            cx = (F[0] + F[2]) / 2
            fw = F[2] - F[0]
            off = max(0.0, (F[2] - 0.52 * W) - F[0]) if cx <= W / 2 else max(0.0, F[2] + (0.48 * W - F[0]) - W)
            if off > 0.2 * fw:                                   # sliding aside would push her face out of the frame: she
                #                                                  stays put and the card sits below her chin instead
                anchor["end"] = {"panel": "bottom", "dx": 0.0, "y0": _r(max(H * 0.5, min(H * 0.6, F[3] - 10)))}
            elif cx <= W / 2:
                anchor["end"] = {"panel": "right", "dx": _r(min(0.0, 0.52 * W - F[2])), "x0": _r(0.56 * W), "fx": _r(F[2])}
            else:
                anchor["end"] = {"panel": "left", "dx": _r(max(0.0, 0.48 * W - F[0])), "x0": _r(0.44 * W), "fx": _r(F[0])}
    return TL


def set_faces(TL, faces):
    """Measured face boxes per presenter segment (fractions of the presenter frame, from Apple Vision) -> layout again."""
    if faces:
        TL["anchor"]["faces"] = {k: list(v) for k, v in faces.items() if v}
    return layout(TL)


# ------------------------------------------------------------------ entry point (timeline.build)
def enrich(TL, spec, vo=None):
    words = word_times(TL, spec, vo)
    TL["words"] = words
    if spec.get("captions") == "kinetic":
        TL["kcaps"] = chunks(words, TL["vo_end"])
    time_graphics(TL, words)
    if spec.get("anchor"):
        build(TL, spec)
    return layout(TL)

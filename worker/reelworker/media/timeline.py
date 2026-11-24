"""Turn (spec + aligned voice) into absolute times for captions, shots and graphics."""

START = 0.10      # first word starts here
TAIL = 2.0        # end card after the last word
GRADES = ("warm_cinematic", "clean")                                                   # media/proedit.py LOOK
SFX_TYPES = ("ambience_airport", "engine_riser", "whoosh", "impact", "ding")            # media/mix.py pro_events


def build(spec: dict, vo: dict, fonts_dir: str, logo: str) -> dict:
    """vo = {group_id: {"file", "trim": [s0, e0], "spans": [[x0, x1], ...]}}  (spans relative to the untrimmed file)."""
    t = START; groups = []; phr = {}
    for g in spec["groups"]:
        v = vo[g["id"]]; s0, e0 = v["trim"]
        spans = [[t + x0 - s0, t + x1 - s0] for x0, x1 in v["spans"]]
        groups.append({"id": g["id"], "file": v["file"], "trim": [s0, e0], "t0": t, "t1": t + (e0 - s0), "spans": spans})
        for k, (a, b) in enumerate(spans):
            phr[(g["id"], k)] = (a, b)
        t += (e0 - s0) + float(g.get("gap_after", 0.5) or 0.5)
    vo_end = groups[-1]["t1"]; end = vo_end + TAIL
    gi = {g["id"]: i for i, g in enumerate(groups)}

    def at(group, phrase):
        if group not in gi: group = groups[0]["id"]
        n = len(groups[gi[group]]["spans"]); phrase = max(0, min(int(phrase or 0), n - 1))
        return phr[(group, phrase)]

    def group_end(group):
        return groups[gi.get(group, 0)]["t1"]

    captions = []
    for g, sg in zip(spec["groups"], groups):
        for k, p in enumerate(g["phrases"][:len(sg["spans"])]):
            a, b = sg["spans"][k]; captions.append({"text": p["caption"], "t0": a, "t1": b})

    # shots: each starts at its phrase (first shot at 0), ends where the next one starts
    shots = sorted(spec["shots"], key=lambda s: at(s["at_group"], s["at_phrase"])[0])
    times = []
    for i, s in enumerate(shots):
        t0 = 0.0 if i == 0 else max(0.0, at(s["at_group"], s["at_phrase"])[0] - 0.08)
        times.append([s["id"], t0])
    clean = []
    for i, (sid, t0) in enumerate(times):           # drop shots shorter than 1.2 s
        t1 = times[i + 1][1] if i + 1 < len(times) else end
        if t1 - t0 >= 1.2 or i == 0:
            clean.append([sid, t0])
    out_shots = []
    for i, (sid, t0) in enumerate(clean):
        t1 = clean[i + 1][1] if i + 1 < len(clean) else end
        z0, z1 = (1.0, 1.10) if i % 2 == 0 else (1.10, 1.0)
        out_shots.append({"id": sid, "t0": round(t0, 3), "t1": round(t1, 3), "z0": z0, "z1": z1, "need": round(t1 - t0 + 0.4, 2)})

    graphics = []
    for g in spec.get("graphics", []):
        a, _ = at(g["at_group"], g.get("at_phrase", 0))
        t0 = max(0.0, a - 0.12)
        if g.get("until_group"):
            t1 = at(g["until_group"], g.get("until_phrase", 0))[0] - 0.05
        else:
            t1 = group_end(g["at_group"]) + 0.15
        if g["type"] in ("flash", "shake"):
            t1 = t0 + 0.6
        t1 = max(t1, t0 + 0.8)
        if g["type"] == "light_leak":                   # a quick sweep, 0.6-1.0 s
            t1 = t0 + min(1.0, max(0.6, float((g.get("params") or {}).get("dur") or 0.8)))
        item = {"type": g["type"], "t0": round(t0, 3), "t1": round(t1, 3), "params": g.get("params") or {}}
        if g["type"] == "list_card":
            starts = sorted(x[0] for x in phr.values() if t0 - 0.01 <= x[0] < t1)
            n = len(item["params"].get("items") or [])
            rev = starts[:n] + [t0 + 0.5 * i for i in range(len(starts[:n]), n)]
            item["reveal"] = sorted(rev)
        graphics.append(item)

    # one graphic per screen zone at a time: a new centre card (or chip) ends the previous one
    zones = {"big_text": "center", "counter": "center", "list_card": "center", "quote_card": "center", "compare": "center",
             "plane_path": "center", "chip": "chip"}
    for zone in ("center", "chip"):
        items = sorted([x for x in graphics if zones.get(x["type"]) == zone], key=lambda x: x["t0"])
        for cur, nxt in zip(items, items[1:]):
            if cur["t1"] > nxt["t0"]:
                cur["t1"] = round(max(cur["t0"] + 0.5, nxt["t0"] + 0.15), 3)
                if cur["type"] == "list_card":
                    cur["reveal"] = [r for r in cur["reveal"] if r < cur["t1"]]

    second = groups[1]["t0"] if len(groups) > 1 else 2.0
    out = {"groups": groups, "captions": captions, "shots": out_shots, "graphics": graphics, "vo_end": round(vo_end, 3),
           "end": round(end, 3), "note_range": [second, groups[-1]["t0"]], "fonts_dir": fonts_dir, "logo": logo,
           "mood": spec.get("mood", "curious")}
    # pro edit layer (opt-in): a whole-video colour grade and place-specific sound design keyed to the script
    if spec.get("grade") in GRADES:
        out["grade"] = spec["grade"]
    sfx = []
    for x in spec.get("sfx") or []:
        if not isinstance(x, dict) or x.get("type") not in SFX_TYPES or x.get("at_group") not in gi:
            continue
        a, _ = at(x["at_group"], x.get("at_phrase", 0))
        u = x.get("until_group") if x.get("until_group") in gi else x["at_group"]
        item = {"type": x["type"], "t0": round(a, 3), "t1": round(max(a + 0.5, group_end(u)), 3)}
        if x.get("level") is not None:
            item["level"] = min(1.0, max(0.0, float(x["level"])))
        sfx.append(item)
    if sfx:
        out["sfx"] = sfx
    # anchor mode, kinetic captions and the kinetic graphics (all opt-in): media/anchor.py
    from . import anchor
    if anchor.needed(spec):
        anchor.enrich(out, spec, vo)
    return out

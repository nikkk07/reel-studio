"""Reel job: script -> voice -> footage -> render -> QC -> publish, each stage checkpointed."""
from datetime import datetime, timezone
import json, re, shutil, subprocess, sys, time
from pathlib import Path
from .. import brain, config, schemas
from .. import brand as brandmod
from ..runner import run_stages, TransientError
from ..media import footage, lexicon, mix, render, timeline

CTA_SAY = "follow करें We One Aviation।"
CTA_CAPTION = "follow karein *We One Aviation*"


def _is_cta(p, name="We One Aviation"):
    """Any spelling of the account's call-to-action line: English, Devanagari respelling (We One), or in the caption."""
    say, cap = p.get("say", ""), p.get("caption", "").replace("*", "")
    n = name.lower()
    return n in say.lower() or n in cap.lower() or (n == "we one aviation" and "वी वन एविएशन" in say)


ACCOUNTS = ("we one aviation", "वी वन एविएशन", "book my charter", "flying star", "legal group")


def _other_account(p, name):
    """A line that names one of the studio's other accounts (each account's reels only ever talk about itself)."""
    t = (p.get("say", "") + " " + p.get("caption", "").replace("*", "")).lower()
    own = name.lower()
    return any(a in t for a in ACCOUNTS if a != own and not (own == "we one aviation" and a == "वी वन एविएशन"))


def _brand(ctx):
    return brandmod.load(ctx.db, ctx.job.get("brand") or "weone")


def _parent_dir(ctx):
    pid = ctx.job.get("parent_id")
    d = config.JOBS_DIR / pid if pid else None
    return d if d and (d / "spec.json").exists() else None


# ------------------------------------------------------------------ helpers
def _say(ctx):
    """Claude's tool activity -> job events (the website's live log), at most one every 2 s."""
    last = [0.0]
    def f(msg):
        if time.time() - last[0] > 2:
            last[0] = time.time(); ctx.log(msg)
    return f

def _spec(ctx):
    return json.loads(ctx.path("spec.json").read_text(encoding="utf-8"))

def _font_file():
    for p in config.FONTS_DIR.rglob("Poppins-SemiBold.ttf"):
        return str(p)
    return ""

def _lexicon_entries():
    try:
        return lexicon.load(str(config.LEXICON))
    except Exception:  # noqa: BLE001
        return []

LEAK = re.compile(r"((?i:https?://|www\.|\bpromo ?code\b|\bwhatsapp\b|\btelegram\b)|\b[\w-]+\.(?:com|in|net|org|io|ly|co|xyz|link|me)\b|@\w{3,}|\b[\w.+-]+@[\w-]+\.\w+)")
PHONE = re.compile(r"(?<![\d+])(?:\+\d{1,3}[\s-]?\d[\d\s-]{7,}\d|(?:0|91[\s-]?)?[6-9]\d{4}[\s-]?\d{5})(?!\d)")


def leaks(spec: dict) -> list:
    """Narration/on-screen text that could advertise someone else (e.g. text planted on a web page Claude read)."""
    texts = [p.get(k, "") for g in spec.get("groups", []) for p in g.get("phrases", []) for k in ("say", "caption")]
    texts += [json.dumps(g.get("params", {}), ensure_ascii=False) for g in spec.get("graphics", [])] + [spec.get("title", "")]
    out = []
    for t in texts:
        t = str(t).replace("@we_one_aviation", "").replace("We One Aviation", "")
        m = LEAK.search(t) or PHONE.search(t)
        if m: out.append(m.group(0))
    return out


def validate_spec(spec: dict, prof: dict = None) -> dict:
    prof = prof or brandmod.LEGACY
    if brandmod.legacy(prof):
        cta_say, cta_cap, name = CTA_SAY, CTA_CAPTION, "We One Aviation"
    else:
        (cta_say, cta_cap), name = brandmod.cta(prof), prof["name"]
    bad = leaks(spec)
    if bad:
        raise TransientError(f"the script contained {bad[0]!r} (web address, phone, e-mail or another account); writing it again")
    ids = []
    for i, g in enumerate(spec["groups"]):
        g["id"] = g.get("id") or f"G{i + 1}"
        if g["id"] in ids: g["id"] = f"G{i + 1}"
        ids.append(g["id"]); g["gap_after"] = float(g.get("gap_after") or 0.5)
        g["phrases"] = [p for p in g["phrases"] if p.get("say", "").strip()] or g["phrases"][:1]
    # exactly one call to action, always the standard one, always last
    for g in spec["groups"]:
        g["phrases"] = [p for p in g["phrases"] if not _is_cta(p, name) and not _other_account(p, name)]
    spec["groups"] = [g for g in spec["groups"] if g["phrases"]] or spec["groups"][:1]
    spec["groups"][-1]["phrases"].append({"say": cta_say, "caption": cta_cap})
    # no line said twice in a row
    for g in spec["groups"]:
        out = []
        for p in g["phrases"]:
            if out and p["caption"].replace("*", "").strip().lower() == out[-1]["caption"].replace("*", "").strip().lower():
                continue
            out.append(p)
        g["phrases"] = out
    ids = [g["id"] for g in spec["groups"]]
    gids = set(ids)
    for k, s in enumerate(spec["shots"]):
        s["id"] = s.get("id") or f"S{k + 1}"
        if s.get("at_group") not in gids: s["at_group"] = ids[0]
    if not any(s["at_group"] == ids[0] and int(s.get("at_phrase", 0)) == 0 for s in spec["shots"]):
        spec["shots"][0]["at_group"], spec["shots"][0]["at_phrase"] = ids[0], 0
    spec["graphics"] = [g for g in spec.get("graphics", []) if g.get("at_group") in gids]
    return spec


# ------------------------------------------------------------------ stages
def stage_script(ctx, prog):
    inp = ctx.job.get("input") or {}
    concept = inp.get("concept") or {"title": inp.get("topic", "")}
    if inp.get("notes"):
        concept = {**concept, "revision_notes": inp["notes"]}
    lex = "\n".join(f"- {e['term']} → {e['say']}" for e in _lexicon_entries())
    parent = _parent_dir(ctx) if inp.get("notes") else None
    prof = _brand(ctx)
    if not brandmod.legacy(prof):            # every other account: its own knowledge, language, tone and call to action
        cs, cc = brandmod.cta(prof)
        common = dict(brand_name=prof["name"], brand_brief=brandmod.brief(prof), language=prof.get("language", "english").title(), cta_say=cs, cta_caption=cc)
        if parent:
            ctx.log("revising the previous version with your notes (unchanged narration and footage are reused)")
            p = brain.prompt("revise_brand", spec=(parent / "spec.json").read_text(encoding="utf-8"), notes=str(inp["notes"])[:2000], **common)
        else:
            p = brain.prompt("script_brand", concept=json.dumps(concept, ensure_ascii=False, indent=1), **common)
    elif parent:
        ctx.log("revising the previous version with your notes (unchanged narration and footage are reused)")
        p = brain.prompt("revise", spec=(parent / "spec.json").read_text(encoding="utf-8"), notes=str(inp["notes"])[:2000], lexicon=lex or "(empty)")
    else:
        p = brain.prompt("script", concept=json.dumps(concept, ensure_ascii=False, indent=1), lexicon=lex or "(empty)")
    prog(0.1)
    ctx.trace("script", "research", "Research sources", "run")
    ctx.trace("script", "write", "Write narration", "wait")
    ctx.trace("script", "facts", "Fact list", "wait", flush=True)
    spec = validate_spec(brain.ask("script", p, schemas.SPEC, ["WebSearch", "WebFetch"], ctx.dir / "brain", timeout=1500, on_event=_say(ctx)), prof)
    ctx.trace("script", "research", "Research sources", "done", f"{len({c['source_url'] for c in spec['claims']})} sources")
    ctx.trace("script", "write", "Write narration", "done", f"{sum(len(g['phrases']) for g in spec['groups'])} lines")
    ctx.trace("script", "facts", "Fact list", "done", f"{len(spec['claims'])} claims", flush=True)
    ctx.path("spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    if parent:   # reuse recorded takes for every narration part whose words did not change
        old = json.loads((parent / "spec.json").read_text(encoding="utf-8")); reused = 0
        old_by_text = {" ".join(p["say"] for p in g["phrases"]): g["id"] for g in old["groups"]}
        (ctx.dir / "vo").mkdir(exist_ok=True)
        for g in spec["groups"]:
            ogid = old_by_text.get(" ".join(p["say"] for p in g["phrases"]))
            if ogid:
                for f in (parent / "vo").glob(f"{ogid}_s*.wav"):
                    shutil.copy(f, ctx.dir / "vo" / f.name.replace(f"{ogid}_s", f"{g['id']}_s", 1)); reused += 1
        ctx.log(f"reusing {reused} voice takes from the previous version")
    ctx.merge_output({"title": spec["title"], "claims": spec["claims"]})
    ctx.log(f"script ready: {spec['title']} ({sum(len(g['phrases']) for g in spec['groups'])} phrases, {len(spec['shots'])} shots)")


def stage_voice(ctx, prog):
    prof = _brand(ctx); hinglish = brandmod.legacy(prof)
    spec = _spec(ctx); E = _lexicon_entries() if hinglish else []; groups = []; unknown = set()
    for g in spec["groups"]:
        says = [lexicon.apply(p["say"], E) if hinglish else p["say"] for p in g["phrases"]]
        for p in g["phrases"]:
            if hinglish: unknown.update(lexicon.unknown_english(p["say"], E))
        groups.append({"id": g["id"], "text": " ".join(says), "phrases_say": says})
    seeds = [11, 23, 37, 51, 63][:max(1, config.TAKES_PER_GROUP)]
    ref = config.VOICE_DIR / "refs" / f"{spec.get('voice', 'female')}.wav"
    if not ref.exists():
        ref = config.VOICE_DIR / "refs" / "female.wav"
    cfg = {"groups": groups, "seeds": seeds, "ref": str(ref), "device": config.DEVICE, "utmos_ckpt": str(config.UTMOS_CKPT),
           "tts_lang": "hi" if hinglish else prof.get("tts_lang", "en"), "asr_lang": "hi" if hinglish else prof.get("asr_lang", "en"),
           "whisper": config.get("REELSTUDIO_WHISPER", "medium"), "threads": 4}
    ctx.path("voice_in.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    total = len(groups) * len(seeds)
    label = {g["id"]: f"Part {i + 1}" for i, g in enumerate(groups)}
    for g in groups:
        ctx.trace("voice", g["id"], label[g["id"]], "wait", f"0/{len(seeds)} takes")
    ctx.trace("voice", "score", "Pick clearest takes", "wait", flush=True)
    errf = ctx.path("voice.err.log")

    def launch(device):
        cfg["device"] = device
        ctx.path("voice_in.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
        # child output goes to a file: an unread pipe fills up (progress bars) and freezes the child
        with open(errf, "ab") as fh:
            return subprocess.Popen([sys.executable, "-m", "reelworker.media.voice", str(ctx.dir)], cwd=Path(__file__).resolve().parents[2],
                                    stdout=fh, stderr=fh)

    p = launch(cfg["device"]); restarts = 0; last_done = -1; last_change = time.time()
    try:
        while True:
            rc = p.poll()
            vo = ctx.dir / "vo"
            counts = [len(list(vo.glob(f"{g['id']}_s*.wav"))) if vo.exists() else 0 for g in groups]
            done = sum(counts); active = next((i for i, n in enumerate(counts) if n < len(seeds)), None)
            for i, (g, n) in enumerate(zip(groups, counts)):
                st = "done" if n >= len(seeds) else "run" if i == active else "wait"
                ctx.trace("voice", g["id"], label[g["id"]], st, f"{n}/{len(seeds)} takes")
            if done >= total:
                ctx.trace("voice", "score", "Pick clearest takes", "run")
            if rc is not None:
                break
            if done != last_done:
                last_done, last_change = done, time.time()
            stall = int(config.get("REELSTUDIO_VOICE_STALL", "420"))
            limit = stall if done < total else stall * 3      # one take never needs 7 min; scoring never 21 min
            if time.time() - last_change > limit:
                p.kill(); p.wait(); restarts += 1
                if restarts > 2:
                    raise TransientError("the voice model stopped responding three times")
                dev = "cpu" if restarts == 2 else cfg["device"]
                ctx.log(f"voice model stopped responding; restarting it{' on CPU' if dev == 'cpu' else ''} (finished takes are kept)", "warn")
                p = launch(dev); last_change = time.time(); continue
            prog(0.85 * done / total); ctx.check_cancel(); time.sleep(5)
    finally:
        if p.poll() is None:
            p.kill()
    if p.returncode != 0:
        tail = errf.read_text(errors="ignore")[-800:] if errf.exists() else ""
        raise RuntimeError(f"voice stage failed: {tail}")
    out = json.loads(ctx.path("voice_out.json").read_text(encoding="utf-8"))
    weak = sorted({w.strip(".,!?।") for g in out.values() for w in g["chosen"]["weak_words"]
                   if len(w.strip(".,!?।")) >= 3 and not any(ch.isdigit() for ch in w)})
    for gid, v in out.items():
        ctx.trace("voice", gid, label.get(gid, gid), "done", f"clarity {v['chosen']['word_prob']:.2f}")
    ctx.trace("voice", "score", "Pick clearest takes", "done", f"{len(weak)} words to check", flush=True)
    ctx.merge_output({"voice": {gid: {k: v["chosen"][k] for k in ("word_prob", "utmos", "transcript")} for gid, v in out.items()},
                      "weak_words": weak, "new_english_words": sorted(unknown)})
    ctx.log(f"voice ready; weak words: {', '.join(weak) or 'none'}")


def _vo_map(ctx):
    return {gid: {"file": v["file"], "trim": v["trim"], "spans": v["spans"]}
            for gid, v in json.loads(ctx.path("voice_out.json").read_text(encoding="utf-8")).items()}


def stage_footage(ctx, prog):
    spec = _spec(ctx); TL = timeline.build(spec, _vo_map(ctx), str(config.FONTS_DIR), str(brandmod.logo(_brand(ctx))))
    need = {s["id"]: s["need"] for s in TL["shots"]}
    shots = [s for s in spec["shots"] if s["id"] in need]
    cands = {}; reuse = {}
    parent = _parent_dir(ctx)
    if parent and (parent / "footage.json").exists():
        pfj = json.loads((parent / "footage.json").read_text(encoding="utf-8"))
        pq = {s["query"]: s["id"] for s in json.loads((parent / "spec.json").read_text(encoding="utf-8"))["shots"]}
        for s in spec["shots"]:
            c = pfj.get("chosen", {}).get(pq.get(s["query"], ""))
            raw = parent / "footage" / "raw" / f"{c['src']}_{c['id']}.mp4" if c else None
            if c and raw.exists():
                reuse[s["id"]] = (c, raw)
        if reuse:
            ctx.log(f"reusing {len(reuse)} clips from the previous version")
    shots_all = shots
    shots = [s for s in shots_all if s["id"] not in reuse]
    for s in shots_all:
        ctx.trace("footage", s["id"], s.get("note") or s["query"], "done" if s["id"] in reuse else "wait", "reused" if s["id"] in reuse else "")
    for k, s in enumerate(shots):
        ctx.trace("footage", s["id"], s.get("note") or s["query"], "run", "searching")
        cands[s["id"]] = footage.search(s["query"], per_source=6)[:12]
        if not cands[s["id"]]:
            cands[s["id"]] = footage.search(" ".join(s["query"].split()[:2]), per_source=6)[:12]
        prog(0.2 * (k + 1) / len(shots))
    empty = [sid for sid, c in cands.items() if not c]
    if shots and len(empty) == len(cands):
        raise TransientError("stock footage search returned nothing (API keys / network?)")
    work = ctx.dir / "footage"; work.mkdir(parents=True, exist_ok=True)
    picks = []
    if shots:
        footage.contact_sheets(shots, {k: v for k, v in cands.items() if v}, work, _font_file())
        desc = "\n".join(f"- {s['id']}: \"{s['query']}\" – {s.get('note', '')} – needs {need[s['id']]:.1f}s" for s in shots if cands[s["id"]])
        ctx.trace("footage", "pick", "Claude chooses clips", "run", flush=True)
        prof = _brand(ctx); vs = prof.get("visual_style") or {}
        look = "" if brandmod.legacy(prof) else "\n".join(["This account's look and rules (follow them strictly; a clip that breaks one is never the pick):",
            f"- Look: {vs.get('look', '')}", *[f"- {r}" for r in vs.get("footage_rules", [])], *[f"- Never: {r}" for r in vs.get("avoid", [])]])
        picks = brain.ask("footage", brain.prompt("footage", shots=desc, niche=prof.get("niche", "aviation"), look=look), schemas.FOOTAGE, ["Read"], work, timeout=900, on_event=_say(ctx))["picks"]
        ctx.trace("footage", "pick", "Claude chooses clips", "done")
    prog(0.35)
    chosen, shot_files = {}, {}
    for sid, (c, raw) in reuse.items():
        try:
            dst = work / "raw" / raw.name; dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy(raw, dst)
            shot_files[sid] = str(footage.prep_shot(dst, work / f"{sid}.mp4", 0.0, need[sid], 0.5)); chosen[sid] = c
        except Exception as e:  # noqa: BLE001
            ctx.log(f"could not reuse clip for {sid}: {e}", "warn")
    pick_map = {p["shot_id"]: p for p in picks}
    for k, s in enumerate(shots):
        sid = s["id"]; lst = cands.get(sid) or []
        p = pick_map.get(sid, {})
        order = [p.get("candidate")] + list(p.get("alternates") or []) + [f"{sid}-{i}" for i in range(len(lst))]
        ok = False
        for label in order:
            try:
                i = int(str(label).split("-")[-1]); c = lst[i]
            except Exception:  # noqa: BLE001
                continue
            try:
                src = footage.download(c, work / "raw" / f"{c['src']}_{c['id']}.mp4")
                out = footage.prep_shot(src, work / f"{sid}.mp4", float(p.get("in_point") or 0) if label == p.get("candidate") else 0.0,
                                        need[sid], float(p.get("focus_x") or 0.5))
                chosen[sid] = {**c, "label": label}; shot_files[sid] = str(out); ok = True; break
            except Exception as e:  # noqa: BLE001
                ctx.log(f"clip {label} unusable: {e}", "warn")
        ctx.trace("footage", sid, s.get("note") or s["query"], "done" if ok else "fail", chosen.get(sid, {}).get("src", "no clip"))
        if not ok:
            ctx.log(f"no usable clip for {sid}; it will be merged into the previous shot", "warn")
        prog(0.35 + 0.65 * (k + 1) / len(shots))
    if not shot_files:
        raise TransientError("no footage could be prepared")
    ctx.path("footage.json").write_text(json.dumps({"files": shot_files, "chosen": chosen, "picks": picks,
                                                     "cands": cands}, ensure_ascii=False, indent=1), encoding="utf-8")
    ctx.merge_output({"credits": footage.credits(chosen)})
    if not shots_all:
        raise TransientError("the script has no shots")


def build_timeline(ctx):
    spec = _spec(ctx); fj = json.loads(ctx.path("footage.json").read_text(encoding="utf-8"))
    spec["shots"] = [s for s in spec["shots"] if s["id"] in fj["files"]]
    prof = _brand(ctx)
    TL = timeline.build(spec, _vo_map(ctx), str(config.FONTS_DIR), str(brandmod.logo(prof)))
    for s in TL["shots"]:
        s["file"] = fj["files"][s["id"]]
    if not brandmod.legacy(prof):             # the account's own look: gold/champagne accents, end card text
        TL["palette"] = prof.get("palette") or {}
        TL["end_title"] = prof.get("end_title", "FOLLOW FOR MORE"); TL["end_sub"] = prof.get("end_sub", "")
    p = ctx.path("render", "timeline.json"); p.write_text(json.dumps(TL, ensure_ascii=False, indent=1), encoding="utf-8")
    return TL, p


def stage_render(ctx, prog):
    TL, tlp = build_timeline(ctx); out = ctx.dir / "render"
    for n, l in (("frames", "Draw frames"), ("mix", "Mix sound"), ("master", "Master & encode"), ("sheet", "Contact sheet")):
        ctx.trace("render", n, l, "wait")
    nfr = int(TL["end"] * 60)
    def vprog(f):
        prog(0.8 * f); ctx.trace("render", "frames", "Draw frames", "run", f"{int(f * nfr)}/{nfr} frames")
    video = render.render_video(tlp, out, TL["end"], config.RENDER_PROCS, vprog)
    ctx.trace("render", "frames", "Draw frames", "done", f"{nfr} frames")
    ctx.trace("render", "mix", "Mix sound", "run", flush=True)
    wav = mix.build(TL, str(config.VOICE_DIR / "refs" / "roomtone.wav"), str(out / "mix.wav")); prog(0.88)
    ctx.trace("render", "mix", "Mix sound", "done"); ctx.trace("render", "master", "Master & encode", "run", flush=True)
    res = render.master(video, Path(wav), out, config.DELIVERY_MAX_MB); prog(0.97)
    ctx.trace("render", "master", "Master & encode", "done", f"{res['lufs']} LUFS · {res['delivery_mb']} MB")
    render.frames_sheet(Path(res["delivery"]), out / "frames.jpg", _font_file())
    ctx.trace("render", "sheet", "Contact sheet", "done", flush=True)
    ctx.path("render", "result.json").write_text(json.dumps(res, indent=1))
    ctx.log(f"rendered {res['duration']}s, {res['delivery_mb']} MB, {res['lufs']} LUFS, peak {res['true_peak']} dBFS")


def stage_qc(ctx, prog):
    res = json.loads(ctx.path("render", "result.json").read_text()); spec = _spec(ctx)
    TL = json.loads(ctx.path("render", "timeline.json").read_text(encoding="utf-8"))
    qdir = ctx.dir / "qc"; qdir.mkdir(exist_ok=True)
    shutil.copy(ctx.dir / "render" / "frames.jpg", qdir / "frames.jpg")
    report = {"title": spec["title"], "captions": [[round(c["t0"], 1), c["text"]] for c in TL["captions"]],
              "shots": [{"id": s["id"], "start": s["t0"], "end": s["t1"], "query": next((x["query"] for x in spec["shots"] if x["id"] == s["id"]), "")}
                        for s in TL["shots"]],
              "loudness": {"lufs": res["lufs"], "true_peak": res["true_peak"]}, "duration": res["duration"],
              "weak_words": (ctx.job.get("output") or {}).get("weak_words", [])}
    (qdir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    auto = {"loudness_ok": res["lufs"] is not None and abs(res["lufs"] + 14) <= 1.0 and (res["true_peak"] or 0) <= -1.0,
            "size_ok": res["delivery_mb"] <= config.DELIVERY_MAX_MB, "duration_ok": 20 <= res["duration"] <= 70}
    prog(0.2)
    ctx.trace("qc", "auto", "Loudness, size, length", "done" if all(auto.values()) else "fail")
    ctx.trace("qc", "watch", "Claude watches the reel", "run", flush=True)
    qc = brain.ask("qc", brain.prompt("qc", brand_name=_brand(ctx)["name"]), schemas.QC, ["Read"], qdir, timeout=600, on_event=_say(ctx))
    ctx.trace("qc", "watch", "Claude watches the reel", "done", f"score {qc['score']}/10", flush=True)
    repick = [i for i in qc["issues"] if i.get("fix") == "repick_shot" and i.get("shot_id")]
    if repick and not ctx.data.get("qc_repaired"):
        fj = json.loads(ctx.path("footage.json").read_text(encoding="utf-8")); changed = False
        for iss in repick:
            sid = iss["shot_id"]; p = next((x for x in fj["picks"] if x["shot_id"] == sid), {})
            used = fj["chosen"].get(sid, {}).get("label"); lst = fj["cands"].get(sid) or []
            for label in list(p.get("alternates") or []) + [f"{sid}-{i}" for i in range(len(lst))]:
                if label == used: continue
                try:
                    c = lst[int(label.split("-")[-1])]
                    src = footage.download(c, ctx.dir / "footage" / "raw" / f"{c['src']}_{c['id']}.mp4")
                    seg = next(s for s in TL["shots"] if s["id"] == sid)
                    footage.prep_shot(src, ctx.dir / "footage" / f"{sid}.mp4", 0.0, seg["need"], 0.5)
                    fj["chosen"][sid] = {**c, "label": label}; changed = True; break
                except Exception:  # noqa: BLE001
                    continue
        if changed:
            ctx.path("footage.json").write_text(json.dumps(fj, ensure_ascii=False, indent=1), encoding="utf-8")
            ctx.data["qc_repaired"] = True; ctx.save()
            ctx.log(f"QC asked to replace {len(repick)} clip(s); re-rendering once", "warn")
            ctx.trace("qc", "fix", f"Replace {len(repick)} clip(s)", "run", flush=True)
            stage_render(ctx, lambda f: prog(0.3 + 0.6 * f))
            ctx.merge_output({"credits": footage.credits(fj["chosen"])})
            ctx.trace("qc", "fix", f"Replace {len(repick)} clip(s)", "done", flush=True)
    ctx.merge_output({"qc": {**qc, **auto}})
    ctx.log(f"QC score {qc['score']}/10, pass={qc['pass']}: {qc['summary'][:200]}")


def stage_publish(ctx, prog):
    res = json.loads(ctx.path("render", "result.json").read_text())
    prefix = ctx.id
    ctx.trace("publish", "video", "Upload reel", "run"); ctx.trace("publish", "archive", "Save master on Mac", "wait", flush=True)
    ctx.db.upload(f"{prefix}/reel.mp4", res["delivery"], "video/mp4"); prog(0.8)
    ctx.db.upload(f"{prefix}/thumb.jpg", res["thumb"], "image/jpeg")
    ctx.trace("publish", "video", "Upload reel", "done", f"{res['delivery_mb']} MB")
    final = config.DELIVERABLES / time.strftime("%Y-%m-%d")
    final.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch for ch in _spec(ctx)["title"] if ch.isalnum() or ch in " -_")[:60].strip().replace(" ", "_") or ctx.id[:8]
    if Path(res["archive"]).exists():
        shutil.copy(res["archive"], final / f"{safe}_{ctx.id[:8]}_master.mp4")
    ctx.trace("publish", "archive", "Save master on Mac", "done", flush=True)
    for junk in [ctx.dir / "render" / "video_full.mp4", ctx.dir / "render" / "master_archive.mp4",
                 ctx.dir / "render" / "mix_lim.wav", ctx.dir / "render" / "qcframes"]:
        try:
            shutil.rmtree(junk) if junk.is_dir() else junk.unlink(missing_ok=True)
        except OSError:
            pass
    TL = json.loads(ctx.path("render", "timeline.json").read_text(encoding="utf-8"))
    script, k = [], 0
    for g in TL["groups"]:
        for a, b in g["spans"]:
            if k < len(TL["captions"]):
                c = TL["captions"][k]; script.append({"t": round(c["t0"], 2), "end": round(c["t1"], 2), "text": c["text"], "group": g["id"]}); k += 1
    ctx.merge_output({"storage_prefix": prefix, "video": f"{prefix}/reel.mp4", "thumb": f"{prefix}/thumb.jpg",
                      "duration": res["duration"], "lufs": res["lufs"], "true_peak": res["true_peak"], "delivery_mb": res["delivery_mb"],
                      "archive_on_mac": str(final / f"{safe}_{ctx.id[:8]}_master.mp4"), "script": script})
    try:     # Instagram caption + Instagram-spec file now, so the cloud can post even if the Mac is off later
        from .. import instagram
        ctx.trace("publish", "insta", "Instagram caption & file", "run", flush=True)
        job = {"id": ctx.id, "brand": ctx.job.get("brand") or "weone", "output": dict(ctx.job.get("output") or {})}
        added = instagram.prepare(ctx.db, job); ctx.job["output"] = job["output"]
        ctx.trace("publish", "insta", "Instagram caption & file", "done", "ready" if added else "", flush=True)
    except Exception as e:  # noqa: BLE001  (the Mac retries this in the background)
        ctx.trace("publish", "insta", "Instagram caption & file", "fail", "retrying later", flush=True)
        ctx.log(f"Instagram caption/file will be prepared shortly ({str(e)[:120]})", "warn")


STAGES = [("script", 8, stage_script), ("voice", 35, stage_voice), ("footage", 12, stage_footage),
          ("render", 30, stage_render), ("qc", 10, stage_qc), ("publish", 5, stage_publish)]


def autopilot_gate(ctx):
    """Autopilot reels post with nobody watching, so a weak result is fixed once automatically, then held for a person."""
    q = (ctx.job.get("output") or {}).get("qc") or {}
    checks = [k for k in ("loudness_ok", "size_ok", "duration_ok") if q.get(k) is False]
    weak = checks or int(q.get("score") or 0) < AUTOPILOT_MIN_SCORE
    if not weak:
        return "ok", ""
    issues = "; ".join(f"at {i.get('t')} s: {i.get('problem')}" for i in (q.get("issues") or [])[:6])
    why = (f"quality check {q.get('score')}/10" + (f", failed {', '.join(checks)}" if checks else "")
           + (f". {q.get('summary', '')}" if q.get("summary") else "") + (f" Issues: {issues}" if issues else ""))[:1500]
    return ("hold" if (ctx.job.get("input") or {}).get("notes") else "revise"), why


AUTOPILOT_MIN_SCORE = int(config.get("REELSTUDIO_AUTOPILOT_MIN_QC", "6"))


def run(ctx):
    run_stages(ctx, STAGES)
    inp = ctx.job.get("input") or {}
    if inp.get("autopilot"):
        verdict, why = autopilot_gate(ctx)
        if verdict == "revise":
            kids = ctx.db.select("jobs", f"select=id&parent_id=eq.{ctx.id}&kind=eq.reel&limit=1")
            if not kids:
                notes = f"Automatic fix by Autopilot. The finished reel scored low: {why}. Fix these problems; keep the topic, the facts and the hook."
                ctx.db.insert("jobs", {"kind": "reel", "parent_id": ctx.id,
                                       "input": {**inp, "notes": notes, "previous_title": (ctx.job.get("output") or {}).get("title")}})
            ctx.finish("cancelled", decision="auto_revise", notes=why, decided_at=datetime.now(timezone.utc).isoformat())
            ctx.log(f"Autopilot: {why[:160]}. Making an improved version automatically.", "warn")
            return
        if verdict == "hold":
            ctx.merge_output({"autopilot_hold": True, "autopilot_note": f"Not posted automatically: {why}"})
            ctx.finish("review")
            ctx.log("Autopilot: still below the quality bar after one automatic fix; held for a person to approve", "warn")
            return
    ctx.finish("review")
    ctx.log("ready for review")

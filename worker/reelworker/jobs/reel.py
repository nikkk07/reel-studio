"""Reel job: script -> voice -> footage -> render -> QC -> publish, each stage checkpointed."""
from datetime import datetime, timezone
import json, re, shutil, subprocess, sys, time
from pathlib import Path
from .. import brain, config, schemas
from .. import agents, lanes
from .. import brand as brandmod
from ..runner import run_stages, TransientError
from ..media import footage, lexicon, mix, render, timeline

CTA_SAY = "follow करें We One Aviation।"
CTA_CAPTION = "follow karein *We One Aviation*"


def _is_cta(p, name="We One Aviation"):
    """Any spelling of the account's call-to-action line: English, Devanagari respelling (We One), or in the caption."""
    say, cap = p.get("say", ""), p.get("caption", "").replace("*", "")
    n = name.lower(); t = (say + " " + cap).lower()
    named = n in t or (n == "we one aviation" and "वी वन एविएशन" in say)
    return named and ("follow" in t or "फॉलो" in say)          # a factual identity line ("We One Aviation: DGCA ground classes") is kept


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
SAY_EN = ("* `say` = what the voice speaks, in clean spoken English. Write numbers, units and times as words (\"one hour ten minutes\", "
          "\"five hundred and forty one kilometres\", \"twenty six percent\"). No symbols, no abbreviations the voice could misread "
          "(say \"air traffic control\", not \"ATC\", unless the abbreviation itself is the point).")
SAY_HI = ("* `say` = what the voice speaks: **Hindi in Devanagari**, English technical words in **English letters**, about 60-70 % English words. "
          "Numbers and years as **English words** (`nineteen seventy-five`, `one thousand`). Use the pronunciation dictionary: write its `say` form for any listed word.\n"
          "Pronunciation dictionary (term -> say):\n{lex}")


def _core_note(prof, concept):
    """Core-lane reels teach the account's own subject; We One adds its academy identity line."""
    if lanes.of(concept.get("lane"), concept.get("category"), concept.get("title"), prof) != "core":
        return ""
    note = ("\n## This is a core reel (the account's own subject)\n" + lanes.rules(prof, "core") +
            "\nTeach the ONE idea exactly as the best expert in this business would: the rule or idea, one simple example or number, and the common mistake people make. ")
    if brandmod.legacy(prof):
        note += ("Just before the CTA you may add ONE short factual identity phrase, e.g. say `We One Aviation में DGCA ground classes, Dwarka और online।` / "
                 "caption `We One Aviation: DGCA ground classes, *Dwarka + online*`. Never claim pass rates, jobs, placements, salaries or course fees.\n\n"
                 "Academy facts you may use:\n" + brandmod.brief(prof, full=False))
    return note


def _sheet_text(fs):
    return json.dumps({"facts": fs.get("facts", []), "angle": fs.get("angle", ""), "do_not_say": fs.get("do_not_say", []),
                       "corrections": fs.get("corrections", [])}, ensure_ascii=False, indent=1)


def check_factsheet(fs):
    probs = []
    good = [f for f in fs.get("facts", []) if str(f.get("source_url", "")).startswith("http")]
    if len(good) < 3:
        probs.append("fewer than 3 facts with a real source URL")
    if not any(f.get("confidence") == "high" for f in good):
        probs.append("no fact is confirmed by an official or primary page (confidence high)")
    return probs


def make_spec_check(prof, fs):
    """Supervisor contract for a script: safe text, right length, facts from the fact sheet, one CTA, own account only."""
    urls = {f.get("source_url", "").rstrip("/") for f in (fs or {}).get("facts", [])}

    def check(spec):
        probs = []
        try:
            validate_spec(json.loads(json.dumps(spec)), prof)
        except TransientError as e:
            probs.append(str(e))
        except Exception as e:  # noqa: BLE001
            probs.append(f"the spec is incomplete: {str(e)[:120]}")
        words = sum(len(p.get("say", "").split()) for g in spec.get("groups", []) for p in g.get("phrases", []))
        if not 55 <= words <= 125:
            probs.append(f"narration has {words} words; it must be 75-100 for a 30-40 s reel")
        claims = spec.get("claims", [])
        if not claims:
            probs.append("no claims with sources")
        if urls and claims and not any(c.get("source_url", "").rstrip("/") in urls for c in claims):
            probs.append("none of the claims comes from the Fact-Checker's sheet; write only from the verified facts")
        return probs
    return check


def critic_text(prof, spec, fs, lane):
    lines = [{"group": g["id"], "say": p.get("say"), "caption": p.get("caption")} for g in spec["groups"] for p in g["phrases"]]
    view = {"title": spec.get("title"), "narration": lines, "graphics": spec.get("graphics", []), "shots": [{k: s.get(k) for k in ("id", "query", "at_group", "at_phrase")} for s in spec.get("shots", [])]}
    return brain.prompt("critic", brand_name=prof["name"], lane=lanes.name(prof, lane), lane_job=lanes.rules(prof, lane), playbook=brain.prompt("ig_playbook"),
                        factsheet=_sheet_text(fs) if fs else "(revision of an approved script: judge against its own claims)", script=json.dumps(view, ensure_ascii=False, indent=1))


def stage_script(ctx, prog):
    inp = ctx.job.get("input") or {}
    concept = inp.get("concept") or {"title": inp.get("topic", "")}
    if inp.get("notes"):
        concept = {**concept, "revision_notes": inp["notes"]}
    lex = "\n".join(f"- {e['term']} → {e['say']}" for e in _lexicon_entries())
    parent = _parent_dir(ctx) if inp.get("notes") else None
    prof = _brand(ctx)
    lane = lanes.of(concept.get("lane"), concept.get("category"), concept.get("title"), prof)
    playbook = brain.prompt("ig_playbook")
    fs = None
    ctx.trace("script", "research", "Fact-Checker", "run")
    ctx.trace("script", "write", "Scriptwriter", "wait")
    ctx.trace("script", "facts", "Hook Critic", "wait", flush=True)
    if not parent:                           # 1. Fact-Checker: verified fact sheet before anything is written
        if "factsheet" not in ctx.data:
            ctx.log("Fact-Checker: researching the topic on official sources")
            ctx.data["factsheet"] = agents.call(ctx, "fact_checker", brain.prompt("factsheet", brand_name=prof["name"], brand_brief=brandmod.brief(prof, full=False),
                                                concept=json.dumps(concept, ensure_ascii=False, indent=1)), schemas.FACTSHEET, ["WebSearch", "WebFetch"],
                                                timeout=1200, check=check_factsheet, on_event=_say(ctx), name="factsheet")
            ctx.save()
        fs = ctx.data["factsheet"]
        ctx.path("factsheet.json").write_text(json.dumps(fs, ensure_ascii=False, indent=1), encoding="utf-8")
    ctx.trace("script", "research", "Fact-Checker", "done", f"{len((fs or {}).get('facts', []))} verified facts" if fs else "kept from the approved version")
    ctx.trace("script", "write", "Scriptwriter", "run", flush=True)
    verify = ("The Fact-Checker has already verified the facts below. Write ONLY from them; do not search again unless a fact you need is missing, and never "
              "contradict the sheet's `do_not_say` or `corrections`." if fs else "Use WebSearch/WebFetch only if the notes need a new fact.")
    block = ("\n\n## Verified fact sheet from the Fact-Checker\n" + _sheet_text(fs) if fs else "") + "\n\n" + playbook + _core_note(prof, concept)
    if not brandmod.legacy(prof):            # every other account: its own knowledge, language, tone and call to action
        cs, cc = brandmod.cta(prof)
        common = dict(brand_name=prof["name"], brand_brief=brandmod.brief(prof), language=prof.get("language", "english").title(), cta_say=cs, cta_caption=cc,
                      say_rules=SAY_HI.format(lex=lex or "(empty)") if brandmod.hinglish(prof) else SAY_EN, verify_note=verify)
        if parent:
            ctx.log("revising the previous version with your notes (unchanged narration and footage are reused)")
            p = brain.prompt("revise_brand", spec=(parent / "spec.json").read_text(encoding="utf-8"), notes=str(inp["notes"])[:2000], **common)
        else:
            p = brain.prompt("script_brand", concept=json.dumps(concept, ensure_ascii=False, indent=1), **common) + block
    elif parent:
        ctx.log("revising the previous version with your notes (unchanged narration and footage are reused)")
        p = brain.prompt("revise", spec=(parent / "spec.json").read_text(encoding="utf-8"), notes=str(inp["notes"])[:2000], lexicon=lex or "(empty)")
    else:
        p = brain.prompt("script", concept=json.dumps(concept, ensure_ascii=False, indent=1), lexicon=lex or "(empty)", academy="", verify_note=verify) + block
    prog(0.1)
    tools = ["WebSearch", "WebFetch"]
    check = make_spec_check(prof, fs)
    raw = agents.call(ctx, "scriptwriter", p, schemas.SPEC, tools, timeout=1500, check=check, on_event=_say(ctx), name="script")
    spec = validate_spec(raw, prof)
    crit = None
    if not parent:                           # 2. Hook Critic: independent review; one full rewrite if it is weak
        ctx.trace("script", "write", "Scriptwriter", "done", f"{sum(len(g['phrases']) for g in spec['groups'])} lines")
        ctx.trace("script", "facts", "Hook Critic", "run", flush=True)
        crit = agents.call(ctx, "hook_critic", critic_text(prof, spec, fs, lane), schemas.CRITIC, [], timeout=600,
                           grade=lambda o: o.get("total"), name="critic")
        ctx.log(f"Hook Critic: {crit.get('total')}/100 (hook {crit.get('hook')}/10, facts {crit.get('fact_fidelity')}/10)")
        if not crit.get("pass"):
            ctx.log("Hook Critic sent the script back for a rewrite")
            p2 = (p + f"\n\n## The Hook Critic reviewed your first draft: {crit.get('total')}/100. Rewrite the WHOLE script fixing these:\n"
                  + "\n".join(f"- {x}" for x in crit.get("fixes", [])) + (f"\nBetter first line: {crit['better_hook']}" if crit.get("better_hook") else "")
                  + "\n\nYour first draft:\n" + json.dumps(raw, ensure_ascii=False)[:12000])
            raw2 = agents.call(ctx, "scriptwriter", p2, schemas.SPEC, tools, timeout=1500, check=check, on_event=_say(ctx), name="script")
            spec2 = validate_spec(raw2, prof)
            crit2 = agents.call(ctx, "hook_critic", critic_text(prof, spec2, fs, lane), schemas.CRITIC, [], timeout=600,
                                grade=lambda o: o.get("total"), name="critic")
            ctx.log(f"Hook Critic on the rewrite: {crit2.get('total')}/100")
            if (crit2.get("total") or 0) >= (crit.get("total") or 0):
                spec, crit = spec2, crit2
    spec["lane"] = lane
    ctx.trace("script", "write", "Scriptwriter", "done", f"{sum(len(g['phrases']) for g in spec['groups'])} lines")
    ctx.trace("script", "facts", "Hook Critic", "done", f"{crit.get('total')}/100" if crit else "revision", flush=True)
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
    out = {"title": spec["title"], "claims": spec["claims"], "lane": lane}
    if crit:
        out["critic"] = {k: crit.get(k) for k in schemas.CRITIC_KEYS + ["total", "pass", "summary"]}
    if fs:
        out["factsheet"] = {"facts": len(fs.get("facts", [])), "sources": sorted({f.get("source_url") for f in fs.get("facts", [])})[:12]}
    ctx.merge_output(out)
    ctx.log(f"script ready: {spec['title']} ({sum(len(g['phrases']) for g in spec['groups'])} phrases, {len(spec['shots'])} shots)")


TTS_RULES = {
    "hi": ("- Write the WHOLE phrase in Devanagari: Hindi words as usual; every English word respelled phonetically in Devanagari the way an educated Indian presenter "
           "says it (registration -> रजिस्ट्रेशन, activation -> एक्टिवेशन, valid -> वैलिड, email -> ईमेल, start -> स्टार्ट, trap -> ट्रैप, document -> डॉक्यूमेंट).\n"
           "- Abbreviations said letter by letter: each letter's name in Devanagari, separated by spaces (DGCA -> डी जी सी ए, PDF -> पी डी एफ़, BVC -> बी वी सी, "
           "JPG -> जे पी जी, FAQ -> एफ़ ए क्यू, CPL -> सी पी एल). Abbreviations normally said as a word stay a word (NASA -> नासा).\n"
           "- Numbers, years, times and units as spoken words in Devanagari, in the language the presenter would use (24 hours -> ट्वेंटी फ़ोर आवर्स, 70% -> सेवेंटी पर्सेंट, "
           "2026 -> ट्वेंटी ट्वेंटी सिक्स). No digits at all.\n"
           "- Names and brands respelled in Devanagari (Flying Star Aviator -> फ़्लाइंग स्टार एविएटर, Pariksha -> परीक्षा, We One Aviation -> वी वन एविएशन).\n"
           "- Punctuation: only comma, । and ? . No dashes, colons, semicolons, brackets, slashes, quotes, ellipses or symbols (% ₹ & + / @). No Latin letters at all."),
    "en": ("- Plain spoken English. Abbreviations said letter by letter as separate capital letters with spaces (D G C A, P D F) unless normally said as a word (NASA).\n"
           "- Numbers, years, times, money and units as words (twenty four hours, seventy percent, twenty twenty six). No digits.\n"
           "- Hindi or Indian words in simple Latin spelling as they are pronounced.\n"
           "- Punctuation: only commas, full stops and question marks. No dashes, colons, semicolons, brackets, slashes, quotes, ellipses or symbols (% ₹ & + / @)."),
}


def _tts_check(lang):
    def check(o):
        probs = []
        for p in o.get("phrases", []):
            t = p.get("tts", "")
            if re.search(r"\d", t):
                probs.append(f"digits left in {p.get('group')}/{p.get('index')}: '{t[:60]}' (write numbers as words)")
            if lang == "hi" and re.search(r"[A-Za-z]", t):
                probs.append(f"Latin letters left in {p.get('group')}/{p.get('index')}: '{t[:60]}' (respell in Devanagari)")
            if re.search(r"[—–:;()\[\]/%₹&+@\"…]", t):
                probs.append(f"symbols left in {p.get('group')}/{p.get('index')}: '{t[:60]}'")
        return probs[:8]
    return check


def _save_terms(ctx, terms):
    """What the Pronunciation Editor learned is kept in the account's own dictionary and reused next time."""
    if not terms:
        return
    try:
        bid = ctx.job.get("brand") or "weone"
        row = (ctx.db.select("brands", f"select=profile&id=eq.{bid}") or [{}])[0]
        prof = row.get("profile") or {}
        lex = {e["term"].lower(): e for e in prof.get("lexicon", [])}
        for t in terms:
            if t.get("term") and t.get("say") and len(lex) < 400:
                lex.setdefault(t["term"].lower(), {"term": t["term"], "say": t["say"]})
        if len(lex) != len(prof.get("lexicon", [])):
            ctx.db.update("brands", f"id=eq.{bid}", {"profile": {**prof, "lexicon": list(lex.values())}})
            brandmod._CACHE.pop(bid, None)
    except Exception as e:  # noqa: BLE001
        ctx.log(f"could not save new pronunciations ({str(e)[:80]})", "warn")


def prepare_tts(ctx, prof, spec, lang, E):
    """Pronunciation Editor: every phrase in a form the voice model reads correctly (captions are untouched)."""
    if "tts" in ctx.data:
        return ctx.data["tts"]
    lex = [{"term": m, "say": e["say"]} for e in E for m in e["match"][:1]] + list(prof.get("lexicon", []))
    phrases = [{"group": g["id"], "index": i, "say": lexicon.apply(p["say"], E) if E else p["say"], "caption": p.get("caption", "")}
               for g in spec["groups"] for i, p in enumerate(g["phrases"])]
    text = brain.prompt("pronounce", brand_name=prof["name"], mode="Hindi (Devanagari) with Indian English words" if lang == "hi" else "English",
                        rules=TTS_RULES[lang], lexicon=json.dumps(lex[:300], ensure_ascii=False) if lex else "(empty)",
                        phrases=json.dumps(phrases, ensure_ascii=False, indent=1))
    res = agents.call(ctx, "pronunciation_editor", text, schemas.PRONOUNCE, [], timeout=600, check=_tts_check(lang), name="pronounce")
    got = {(p["group"], int(p["index"])): p["tts"].strip() for p in res.get("phrases", []) if p.get("tts", "").strip()}
    tts = {g["id"]: [got.get((g["id"], i)) or ph["say"] for i, ph in enumerate(g["phrases"])] for g in spec["groups"]}
    ctx.data["tts"] = tts; ctx.save()
    _save_terms(ctx, res.get("terms", []))
    return tts


def _run_voice(ctx, cfg, cfg_name, out_name, prog, lo, hi, label):
    """Runs the voice model in its own process, watching for stalls; returns its output."""
    groups, seeds = cfg["groups"], cfg["seeds"]
    total = len(groups) * len(seeds)
    errf = ctx.path("voice.err.log")

    def launch(device):
        cfg["device"] = device
        ctx.path(cfg_name).write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
        with open(errf, "ab") as fh:         # child output goes to a file: an unread pipe fills up and freezes the child
            return subprocess.Popen([sys.executable, "-m", "reelworker.media.voice", str(ctx.dir), cfg_name, out_name], cwd=Path(__file__).resolve().parents[2],
                                    stdout=fh, stderr=fh)

    p = launch(cfg["device"]); restarts = 0; last_done = -1; last_change = time.time()
    try:
        while True:
            rc = p.poll()
            vo = ctx.dir / "vo"
            counts = [sum(1 for s in seeds if (vo / f"{g['id']}_s{s}.wav").exists()) for g in groups]
            done = sum(counts); active = next((i for i, n in enumerate(counts) if n < len(seeds)), None)
            for i, (g, n) in enumerate(zip(groups, counts)):
                st = "done" if n >= len(seeds) else "run" if i == active else "wait"
                ctx.trace("voice", g["id"], label.get(g["id"], g["id"]), st, f"{n}/{len(seeds)} takes")
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
            prog(lo + (hi - lo) * done / max(1, total)); ctx.check_cancel(); time.sleep(5)
    finally:
        if p.poll() is None:
            p.kill()
    if p.returncode != 0:
        tail = errf.read_text(errors="ignore")[-800:] if errf.exists() else ""
        raise RuntimeError(f"voice stage failed: {tail}")
    return json.loads(ctx.path(out_name).read_text(encoding="utf-8"))


def _inspect(ctx, prof, out):
    """Voice Inspector: compares what should be said with what the recogniser heard in every take; picks the take said right."""
    parts = [{"id": gid, "intended": v["chosen"].get("text") or "",
              "takes": [{"take": Path(t["file"]).stem, "transcript": t["transcript"]} for t in v["takes"]]} for gid, v in out.items()]
    res = agents.call(ctx, "voice_inspector", brain.prompt("pron_check", brand_name=prof["name"], parts=json.dumps(parts, ensure_ascii=False, indent=1)),
                      schemas.PRONCHECK, [], timeout=600, name="pron_check",
                      grade=lambda o: 100 * sum(1 for p in o.get("parts", []) if p.get("ok")) / max(1, len(o.get("parts", []))))
    verdict = {}
    for p in res.get("parts", []):
        v = out.get(p.get("id"))
        if not v:
            continue
        pick = next((t for t in v["takes"] if Path(t["file"]).stem == p.get("best_take")), None)
        if pick:                                  # the take that is pronounced right wins over the one that merely scored clearest
            v.update(file=pick["file"], trim=pick["trim"], spans=pick["spans"], chosen=pick)
        verdict[p["id"]] = p
    return verdict


def stage_voice(ctx, prog):
    prof = _brand(ctx); hinglish = brandmod.legacy(prof) or brandmod.hinglish(prof)     # Hindi voice + pronunciation dictionary
    lang = "hi" if hinglish else prof.get("tts_lang", "en")
    spec = _spec(ctx); E = _lexicon_entries() if hinglish else []
    unknown = set()
    if hinglish:
        for g in spec["groups"]:
            for p in g["phrases"]:
                unknown.update(lexicon.unknown_english(p["say"], E))
    parent = _parent_dir(ctx) if (ctx.job.get("input") or {}).get("notes") else None
    if parent and "tts" not in ctx.data:
        pst = json.loads((parent / "state.json").read_text()) if (parent / "state.json").exists() else {}
        if "tts" not in (pst.get("data") or {}):          # takes recorded before the Pronunciation Editor existed are never reused
            for f in (ctx.dir / "vo").glob("*.wav"):
                f.unlink()
    ctx.trace("voice", "pron", "Pronunciation Editor", "run", flush=True)
    tts = prepare_tts(ctx, prof, spec, "hi" if hinglish else "en", E)
    ctx.trace("voice", "pron", "Pronunciation Editor", "done", f"{sum(len(v) for v in tts.values())} lines")
    groups = [{"id": g["id"], "text": " ".join(tts[g["id"]]), "phrases_say": tts[g["id"]]} for g in spec["groups"]]
    seeds = [11, 23, 37, 51, 63][:max(1, config.TAKES_PER_GROUP)]
    ref = config.VOICE_DIR / "refs" / f"{spec.get('voice', 'female')}.wav"
    if not ref.exists():
        ref = config.VOICE_DIR / "refs" / "female.wav"
    base = {"ref": str(ref), "device": config.DEVICE, "utmos_ckpt": str(config.UTMOS_CKPT), "tts_lang": lang,
            "asr_lang": "hi" if hinglish else prof.get("asr_lang", "en"), "whisper": config.get("REELSTUDIO_WHISPER", "medium"), "threads": 4}
    label = {g["id"]: f"Part {i + 1}" for i, g in enumerate(groups)}
    for g in groups:
        ctx.trace("voice", g["id"], label[g["id"]], "wait", f"0/{len(seeds)} takes")
    ctx.trace("voice", "score", "Pick clearest takes", "wait", flush=True)
    if ctx.path("voice_out.json").exists() and ctx.data.get("voice_checked"):
        out = json.loads(ctx.path("voice_out.json").read_text(encoding="utf-8"))
    else:
        out = _run_voice(ctx, {**base, "groups": groups, "seeds": seeds}, "voice_in.json", "voice_out.json", prog, 0.0, 0.6, label)
    # Voice Inspector: listen (via the recogniser) and redo any part that is not pronounced right, up to twice with better spellings
    issues = {}
    for rnd, extra in enumerate(([71, 83, 97], [109, 127, 131])):
        ctx.trace("voice", "check", "Voice Inspector", "run", flush=True)
        verdict = _inspect(ctx, prof, out)
        bad = {gid: v for gid, v in verdict.items() if not v.get("ok")}
        issues = {gid: [f"{x.get('intended')} -> heard {x.get('heard')}" for x in v.get("problems", [])] for gid, v in bad.items()}
        ctx.path("voice_out.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        if not bad:
            break
        ctx.log(f"Voice Inspector: {sum(len(v) for v in issues.values())} word(s) not pronounced right in {len(bad)} part(s); recording them again"
                f" ({'; '.join(x for v in issues.values() for x in v)[:200]})", "warn")
        redo = []
        for gid, v in bad.items():
            lines = list(tts[gid])
            for x in v.get("problems", []):
                if x.get("intended") and x.get("fix_say"):
                    lines = [l.replace(x["intended"], x["fix_say"]) for l in lines]
            tts[gid] = lines
            redo.append({"id": gid, "text": " ".join(lines), "phrases_say": lines})
        ctx.data["tts"] = tts; ctx.save()
        _save_terms(ctx, [{"term": x["intended"], "say": x["fix_say"]} for v in bad.values() for x in v.get("problems", []) if x.get("intended") and x.get("fix_say")])
        more = _run_voice(ctx, {**base, "groups": redo, "seeds": extra}, f"voice_redo{rnd + 1}.json", f"voice_redo{rnd + 1}_out.json",
                          prog, 0.6 + 0.15 * rnd, 0.75 + 0.15 * rnd, label)
        for gid, v in more.items():
            out[gid]["takes"] = out[gid]["takes"] + v["takes"]
            out[gid].update(file=v["file"], trim=v["trim"], spans=v["spans"], chosen=v["chosen"])
        if rnd == 1:                                 # the last redo still gets one final listen
            verdict = _inspect(ctx, prof, out)
            bad = {gid: v for gid, v in verdict.items() if not v.get("ok")}
            issues = {gid: [f"{x.get('intended')} -> heard {x.get('heard')}" for x in v.get("problems", [])] for gid, v in bad.items()}
    ctx.path("voice_out.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    ctx.data["voice_checked"] = True; ctx.save()
    ctx.trace("voice", "check", "Voice Inspector", "done" if not issues else "fail", "all words right" if not issues else f"{sum(len(v) for v in issues.values())} word(s) still off")
    weak = sorted({w.strip(".,!?।") for g in out.values() for w in g["chosen"]["weak_words"]
                   if len(w.strip(".,!?।")) >= 3 and not any(ch.isdigit() for ch in w)})
    for gid, v in out.items():
        ctx.trace("voice", gid, label.get(gid, gid), "done", f"clarity {v['chosen']['word_prob']:.2f}")
    ctx.trace("voice", "score", "Pick clearest takes", "done", f"{len(weak)} words to check", flush=True)
    upd = {"voice": {gid: {k: v["chosen"][k] for k in ("word_prob", "utmos", "transcript")} for gid, v in out.items()},
           "weak_words": weak, "new_english_words": sorted(unknown), "voice_issues": issues}
    if issues:                                       # never auto-post a reel with a word said wrong; a person decides
        upd["hold"] = True
        upd["hold_reason"] = "Pronunciation: " + "; ".join(x for v in issues.values() for x in v)[:300]
    ctx.merge_output(upd)
    ctx.log(f"voice ready; pronunciation {'all correct' if not issues else 'issues: ' + '; '.join(x for v in issues.values() for x in v)[:200]}")


def _vo_map(ctx):
    return {gid: {"file": v["file"], "trim": v["trim"], "spans": v["spans"]}
            for gid, v in json.loads(ctx.path("voice_out.json").read_text(encoding="utf-8")).items()}


def _look(prof):
    vs = prof.get("visual_style") or {}
    if not vs:
        return ""
    return "\n".join(["This account's look and rules (follow them strictly; a clip that breaks one is never the pick):",
                      f"- Look: {vs.get('look', '')}", *[f"- {r}" for r in vs.get("footage_rules", [])], *[f"- Never: {r}" for r in vs.get("avoid", [])]])


def _pick(ctx, text, work):
    """Footage Picker agent: reads the contact sheets in the footage folder."""
    return agents.call(ctx, "footage_picker", text, schemas.FOOTAGE, ["Read"], timeout=900, on_event=_say(ctx), name="footage", cwd=work)["picks"]


def stage_footage(ctx, prog):
    spec = _spec(ctx); TL = timeline.build(spec, _vo_map(ctx), str(config.FONTS_DIR), str(brandmod.logo(_brand(ctx), ctx.db)))
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
        prof = _brand(ctx); look = _look(prof)
        picks = _pick(ctx, brain.prompt("footage", shots=desc, niche=prof.get("niche", "aviation"), look=look), work)
        # no clip fits: search once more with Claude's better query and pick again (never fill the slot with a clip that contradicts the line)
        retry = {p["shot_id"]: p["new_query"].strip() for p in picks if str(p.get("candidate")).lower() == "none" and str(p.get("new_query") or "").strip()}
        retry = {sid: q for sid, q in retry.items() if sid in {s["id"] for s in shots}}
        if retry:
            ctx.log(f"no fitting clip for {', '.join(retry)}; searching again")
            again = [{**s, "query": retry[s["id"]]} for s in shots if s["id"] in retry]
            for s in again:
                cands[s["id"]] = footage.search(s["query"], per_source=6)[:12] or cands[s["id"]]
            footage.contact_sheets(again, {s["id"]: cands[s["id"]] for s in again if cands[s["id"]]}, work, _font_file())
            desc2 = "\n".join(f"- {s['id']}: \"{s['query']}\" – {s.get('note', '')} – needs {need[s['id']]:.1f}s" for s in again if cands[s["id"]])
            more = _pick(ctx, brain.prompt("footage", shots=desc2, niche=prof.get("niche", "aviation"), look=look), work)
            picks = [p for p in picks if p["shot_id"] not in retry] + more
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
        refused = str(p.get("candidate")).lower() == "none"
        order = [] if refused else [p.get("candidate")] + list(p.get("alternates") or [])
        if not refused or k == 0 and sid == shots_all[0]["id"]:     # Claude said no clip fits: leave the slot out (merged into the previous shot), except the opening shot
            order += [f"{sid}-{i}" for i in range(len(lst))]
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
    TL = timeline.build(spec, _vo_map(ctx), str(config.FONTS_DIR), str(brandmod.logo(prof, ctx.db)))
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
    qc = agents.call(ctx, "quality_inspector", brain.prompt("qc", brand_name=_brand(ctx)["name"]), schemas.QC, ["Read"], timeout=600, on_event=_say(ctx), name="qc", cwd=qdir,
                     grade=lambda o: 10 * float(o.get("score") or 0))
    ctx.trace("qc", "watch", "Claude watches the reel", "done", f"score {qc['score']}/10", flush=True)
    repick = [i for i in qc["issues"] if i.get("fix") == "repick_shot" and i.get("shot_id")]
    if repick and not ctx.data.get("qc_repaired"):
        fj = json.loads(ctx.path("footage.json").read_text(encoding="utf-8")); changed = False
        spec = _spec(ctx); q = {x["id"]: x for x in spec["shots"]}; first = spec["shots"][0]["id"]
        todo = [i for i in repick if i["shot_id"] in fj.get("files", {}) and fj.get("cands", {}).get(i["shot_id"])]
        if todo:
            need = {s["id"]: s["need"] for s in TL["shots"]}; prof = _brand(ctx)
            desc = "\n".join(f"- {i['shot_id']}: \"{q.get(i['shot_id'], {}).get('query', '')}\" – {q.get(i['shot_id'], {}).get('note', '')} – needs {need.get(i['shot_id'], 3):.1f}s"
                             f" – the Quality Inspector REJECTED the current clip ({fj['chosen'].get(i['shot_id'], {}).get('label')}): {i.get('problem', '')}" for i in todo)
            picks = _pick(ctx, brain.prompt("footage", shots=desc, niche=prof.get("niche", "aviation"), look=_look(prof)) +
                          "\n\nPick a DIFFERENT candidate that fixes the Quality Inspector's problem, or `none` if no candidate fits.", ctx.dir / "footage")
            pm = {x["shot_id"]: x for x in picks}
            for iss in todo:
                sid = iss["shot_id"]; p = pm.get(sid, {}); lst = fj["cands"][sid]; used = fj["chosen"].get(sid, {}).get("label")
                labels = [] if str(p.get("candidate")).lower() == "none" else [p.get("candidate")] + list(p.get("alternates") or [])
                ok = False
                for label in labels:
                    if not label or label == used: continue
                    try:
                        c = lst[int(str(label).split("-")[-1])]
                        src = footage.download(c, ctx.dir / "footage" / "raw" / f"{c['src']}_{c['id']}.mp4")
                        footage.prep_shot(src, ctx.dir / "footage" / f"{sid}.mp4", float(p.get("in_point") or 0), need.get(sid, 3), float(p.get("focus_x") or 0.5))
                        fj["chosen"][sid] = {**c, "label": label}; ok = changed = True; break
                    except Exception:  # noqa: BLE001
                        continue
                if not ok and sid != first:          # nothing fits: drop the shot (the previous one runs longer) rather than show a wrong clip
                    fj["files"].pop(sid, None); fj["chosen"].pop(sid, None); changed = True
                    ctx.log(f"no better clip for {sid}; the previous shot covers it")
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

"""Make a dialogue reel (two or three AI characters talking) from a spec, on this Mac with free tools only.

    caffeinate -i ~/ReelStudio/.venv/bin/python worker/scripts/dialogue_reel.py /Volumes/AI-Models/nios-ad/spec.json [--crm]

Stages (each is kept; running the same command again carries on): pictures -> voices (Gemini TTS, keys in the Keychain)
-> lip-sync of the "lip" lines (Kaggle, two at a time, overnight) -> edit + mix + master -> reel.mp4 (+ CRM > Content with --crm).
--voices-only stops after the voices (to listen before the GPU hours are spent).  --no-lips skips Kaggle (stills only).

Spec (JSON):
  title, images (folder), work (folder), mood, ai_label, end_title, end_sub, end_hold
  characters: {who: {"voice": Gemini voice name, "style": default delivery, "look": how they move (lip-sync prompt)}}
  lines: [{"id", "who", "say" (what is spoken), "cap" (on-screen text, *keyword*), "style" (delivery, optional),
           "shot": picture name, "lip": true|false, "pause": s after, "beat": {"img", "dur", "gray"} (silent hold after),
           "pics": [{"img"|"stock": name|file, "at": s, "lip", "gray", "off", "z": [z0, z1]}] (several pictures in one line)}]
  stock: {name: video file}     graphics: [{"type", "line", "at", "dur", "params", "reveal_every"}]
"""
import argparse, json, os, shutil, subprocess, sys, time, uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reelworker import config  # noqa: E402
from reelworker.media import dialogue as dl, presenter, render, mix  # noqa: E402


def log(m):
    print(f"{datetime.now():%H:%M:%S} {m}", flush=True)


def main():
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("spec"); a.add_argument("--crm", action="store_true")
    a.add_argument("--voices-only", action="store_true"); a.add_argument("--no-lips", action="store_true")
    a.add_argument("--send-only", action="store_true", help="send the lip-sync shots to Kaggle and stop (collect later)")
    o = a.parse_args()
    spec = json.loads(Path(o.spec).read_text(encoding="utf-8"))
    imgs, work = Path(spec["images"]), Path(spec["work"]); work.mkdir(parents=True, exist_ok=True)
    stock = {k: Path(v) for k, v in (spec.get("stock") or {}).items()}

    # 1) pictures: every one the spec uses must be there before anything is spent
    pics_used = set()
    for ln in spec["lines"]:
        for p in ln.get("pics") or [{"img": ln["shot"]}]:
            if p.get("img"):
                pics_used.add(p["img"])
        if ln.get("beat", {}).get("img"):
            pics_used.add(ln["beat"]["img"])
    missing = sorted(n for n in pics_used if not dl.find_image(imgs, n))
    missing += sorted(f"stock {k}" for k, v in stock.items() if not v.exists())
    if missing:
        raise SystemExit("missing pictures/clips: " + ", ".join(missing))
    framed = {n: dl.frame_916(dl.find_image(imgs, n), work / "img" / f"{n}.png") for n in pics_used}
    log(f"pictures: {len(framed)} ready (9:16)")

    # 2) voices
    ch = spec["characters"]; vdir = work / "vo"; todo = [ln for ln in spec["lines"] if not (vdir / f"{ln['id']}.wav").exists()]
    if todo:
        ks = dl.keys()
        if not ks:
            raise SystemExit("no Gemini key in the Keychain: run  security add-generic-password -U -s reelstudio-gemini -a key1 -w")
        for ln in todo:
            c = ch[ln["who"]]
            dl.tts(ln["say"], c["voice"], ln.get("style") or c.get("style", ""), ks, vdir / f"{ln['id']}.wav", log)
    durs = {ln["id"]: round(dl.seconds(vdir / f"{ln['id']}.wav"), 3) for ln in spec["lines"]}
    P = dl.plan(spec, durs); (work / "plan.json").write_text(json.dumps(P, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"voices: {len(durs)} lines, reel {P['end']:.1f} s")
    if o.voices_only:
        listen = work / "voices_preview.wav"
        TLv = {"groups": [{"file": str(vdir / f"{x['id']}.wav"), "trim": [0, durs[x['id']]], "t0": x["t0"]} for x in P["lines"]]}
        presenter.voice_track(TLv["groups"], P["vo_end"], listen)
        log(f"listen: {listen}"); return

    # 3) lip-sync (Kaggle): the "lip" pictures, longest-priority first if the week's GPU time is short
    L = {x["id"]: x for x in P["lines"]}; jobs = []
    for p in P["pics"]:
        if not p.get("lip") or o.no_lips:
            continue
        ln = L[p["line"]]; jid = f"{p['line']}_{p['img']}"
        if p["t0"] > ln["t0"]:                                   # only a line's first picture can talk (it carries the whole line)
            log(f"{jid}: lip-sync only works on a line's first picture; shown as a still"); continue
        lead = max(0.0, ln["t0"] - p["t0"])                      # the picture starts a moment before the words
        if lead + ln["t1"] - ln["t0"] > presenter.MAX_S:
            log(f"{jid}: line is {ln['t1'] - ln['t0']:.1f} s, longer than one talking clip ({presenter.MAX_S} s); the end holds still")
        aud = work / "lip" / f"{jid}.wav"; aud.parent.mkdir(parents=True, exist_ok=True)
        if not aud.exists():
            dur = min(presenter.MAX_S, lead + (ln["t1"] - ln["t0"]) + 0.3)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(vdir / f"{ln['id']}.wav"), "-af",
                            f"adelay={int(lead * 1000)}:all=1,apad=whole_dur={dur:.3f}", "-t", f"{dur:.3f}", "-ar", "16000", "-ac", "1",
                            "-c:a", "pcm_s16le", str(aud)], check=True)
        look = ch[ln["who"]].get("look", "")
        jobs.append({"id": jid, "audio": str(aud), "image": str(framed[p["img"]]), "prio": float(ln.get("prio", 0)),
                     "prompt": f"{look} Lips move exactly with the speech; natural blinking and small head movements; "
                               f"silent and still when there is no speech. Camera locked, no zoom."})
    st = json.loads((work / "kaggle.json").read_text()) if (work / "kaggle.json").exists() else {}
    sent = {i for bb in st.get("_batches", {}).values() for i in bb["ids"]}
    new = [j for j in jobs if j["id"] not in st and j["id"] not in sent]
    left = presenter.budget()["left_h"]; can = int(left // (presenter.CLIP_H * 0.85))       # batches load the weights once
    if len(new) > can:
        keep = sorted(new, key=lambda j: -j["prio"])[:max(0, can)]; skip = [j["id"] for j in new if j not in keep]
        log(f"Kaggle time left this week {left:.1f} h: {len(skip)} lip-sync shot(s) become stills: {', '.join(skip)}")
        jobs = [j for j in jobs if j["id"] in st or j["id"] in sent or j in keep]
    done = dl.lipsync(jobs, work, log, wait=not o.send_only) if jobs else {}
    log(f"lip-sync: {len(done)} of {len(jobs)} shot(s)")
    kst = json.loads((work / "kaggle.json").read_text()) if (work / "kaggle.json").exists() else {}
    if any(bb.get("state") == "running" for bb in kst.get("_batches", {}).values()):
        log("Kaggle is still rendering; run the same command again later to collect the clips and finish the reel"); return

    # 4) shots on the reel's clock
    sdir = work / "shots"; sdir.mkdir(exist_ok=True); shots = []
    for i, p in enumerate(P["pics"]):
        sid = f"s{i:02d}"; dur = p["t1"] - p["t0"]; ln = L[p["line"]]; out = sdir / f"{sid}.mp4"
        jid = f"{p['line']}_{p.get('img')}"
        if p.get("stock"):
            dl.video_clip(stock[p["stock"]], out, dur, float(p.get("off", 0.0))); z = p.get("z", [1.0, 1.06])
        elif p.get("lip") and jid in done:
            dl.video_clip(done[jid], out, dur, 0.0); z = p.get("z", [1.0, 1.035])     # its audio started with this picture
        else:
            dl.still_clip(framed[p["img"]], out, dur, gray=p.get("gray", False)); z = p.get("z", [1.0, 1.09])
        shots.append({"id": sid, "file": str(out), "t0": p["t0"], "t1": p["t1"], "z0": z[0], "z1": z[1],
                      "presenter": bool(p.get("lip") and jid in done) or bool(p.get("face"))})
    log(f"edit: {len(shots)} shots")

    # 5) timeline -> frames -> mix -> master
    TL = {"shots": shots, "graphics": dl.graphics(spec, P), "captions": dl.captions(P["lines"]),
          "groups": [{"file": str(vdir / f"{x['id']}.wav"), "trim": [0, durs[x["id"]]], "t0": x["t0"]} for x in P["lines"]],
          "vo_end": P["vo_end"], "end": P["end"], "fonts_dir": str(config.FONTS_DIR), "logo": str(config.LOGO),
          "mood": spec.get("mood", "uplifting"), "sfx": spec.get("sfx", []),
          "end_title": spec.get("end_title", "FREE CAREER COUNSELLING")[:40], "end_sub": spec.get("end_sub", "")[:60]}
    if spec.get("ai_label"):
        TL["ai_label"] = str(spec["ai_label"])[:40]
    out = work / "render"; out.mkdir(exist_ok=True)
    tlp = out / "timeline.json"; tlp.write_text(json.dumps(TL, ensure_ascii=False, indent=1), encoding="utf-8")
    video = render.render_video(tlp, out, TL["end"], config.RENDER_PROCS, lambda f: None)
    wav = mix.build(TL, str(config.VOICE_DIR / "refs" / "roomtone.wav"), str(out / "mix.wav"))
    res = render.master(video, Path(wav), out, 45)
    final = work.parent / f"{spec.get('slug', 'dialogue')}_{datetime.now():%Y%m%d_%H%M}.mp4"
    shutil.copy(res["delivery"], final)
    (work / "result.json").write_text(json.dumps({**res, "final": str(final)}, indent=1))
    log(f"finished: {final} ({res['duration']} s, {res['delivery_mb']} MB, {res['lufs']} LUFS)")

    # 6) CRM > Content (Ad creatives), for the owner's approval
    if o.crm:
        try:
            from reelworker import crm
            if not crm.linked():
                raise RuntimeError("the CRM link is not set on this Mac")
            jid_p = work / "crm_job.txt"
            jid = jid_p.read_text().strip() if jid_p.exists() else str(uuid.uuid4()); jid_p.write_text(jid)
            script = "\n".join(f"{ch[x['who']].get('name', x['who'])}: {x['cap'].replace('*', '')}" for x in spec["lines"])
            r = crm.call("adopt", studio_job=jid, title=spec["title"][:120], script=script[:3000],
                         summary=" / ".join(x["cap"].replace("*", "") for x in spec["lines"][:2])[:600])
            if not r.get("ok"):
                raise RuntimeError(r.get("detail"))
            item = int(r["id"]); cover = crm._cover(final, work / "cover.jpg")
            links = crm.call("video_upload", id=item)
            crm._put(links["video"]["url"], final, "video/mp4"); crm._put(links["thumb"]["url"], cover, "image/jpeg")
            r = crm.call("video_done", id=item, seconds=res["duration"])
            log(f"CRM > Content > Ad creatives: item {item} {'ready for approval' if r.get('ok') else r.get('detail')}")
        except Exception as e:  # noqa: BLE001
            log(f"CRM upload skipped: {str(e)[:200]} (the video is at {final})")


if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parents[1])
    main()

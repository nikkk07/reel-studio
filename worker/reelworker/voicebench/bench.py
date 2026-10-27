"""Voice model benchmark on the pronunciation test set (testset.json).

    python -m reelworker.voicebench.bench --ckpt <dir with ve.pt, grapheme json, t3_*.safetensors, s3gen*> --ref female.wav
        --variants v2,v3,hi --seeds 11,23 --out <dir> [--lines we1,bm2]

Each variant = (text model, speech decoder). Every take is scored by:
  * term accuracy - each technical term of the line must be heard (fuzzy match in an independent Whisper transcript)
  * CER           - character error rate of the whole line
  * speaker sim   - cosine similarity of the take's voice to the reference voice (same voice = close to 1)
Writes <out>/results.json and one wav per take, so takes can be compared by ear.
"""
import argparse, difflib, json, re, time, unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
VARIANTS = {               # name -> (T3 text-to-token model, S3Gen decoder)
    "v2": ("t3_mtl23ls_v2.safetensors", "s3gen.pt"),
    "v3": ("t3_mtl23ls_v3.safetensors", "s3gen_v3.safetensors"),
    "v3old": ("t3_mtl23ls_v3.safetensors", "s3gen.pt"),
    "hi": ("t3_hi.safetensors", "s3gen_v3.safetensors"),
}


def load_tts(ckpt: Path, t3_file: str, s3_file: str, device="cpu"):
    """Like ChatterboxMultilingualTTS.from_local, but the speech decoder file is chosen (V3 ships its own)."""
    import torch
    from safetensors.torch import load_file
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS, Conditionals
    from chatterbox.models.t3 import T3
    from chatterbox.models.t3.modules.t3_config import T3Config
    from chatterbox.models.s3gen import S3Gen
    from chatterbox.models.tokenizers import MTLTokenizer
    from chatterbox.models.voice_encoder import VoiceEncoder
    loc = torch.device("cpu")
    ve = VoiceEncoder(); ve.load_state_dict(torch.load(ckpt / "ve.pt", map_location=loc, weights_only=True)); ve.to(device).eval()
    t3 = T3(T3Config.multilingual()); st = load_file(str(_find(ckpt, t3_file)))
    if "model" in st: st = st["model"][0]
    t3.load_state_dict(st); t3.to(device).eval()
    s3 = S3Gen(); p = _find(ckpt, s3_file)
    sd = load_file(str(p)) if p.suffix == ".safetensors" else torch.load(p, map_location=loc, weights_only=True)
    miss, extra = s3.load_state_dict(sd, strict=False)        # .safetensors leaves out two fixed buffers (mel filters, window)
    bad = [k for k in miss if k not in ("tokenizer._mel_filters", "tokenizer.window")] + list(extra)
    if bad:
        raise RuntimeError(f"speech decoder {s3_file} does not fit: {bad[:5]}")
    s3.to(device).eval()
    tok = MTLTokenizer(str(ckpt / "grapheme_mtl_merged_expanded_v1.json"))
    conds = Conditionals.load(ckpt / "conds.pt", map_location=loc).to(device) if (ckpt / "conds.pt").exists() else None
    return ChatterboxMultilingualTTS(t3, s3, ve, tok, device, conds=conds)


def _find(ckpt: Path, name: str) -> Path:
    for p in [ckpt / name, *ckpt.rglob(name)]:
        if p.exists():
            return p
    raise FileNotFoundError(name)


# ------------------------------------------------------------------ scoring
def norm_hi(s: str) -> str:
    s = unicodedata.normalize("NFC", s)
    s = s.replace("़", "")                                   # nukta: फ़ = फ
    s = re.sub("[ँं]", "ं", s)                    # chandrabindu = anusvara
    s = re.sub(r"[^ऀ-ॿa-z0-9 ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def norm_en(s: str) -> str:
    s = s.lower().replace("-", " ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\b([a-z])\s+(?=[a-z]\b)", r"\1", s)             # "d g c a" -> "dgca"
    return re.sub(r"\s+", " ", s).strip()


def term_hit(term: str, text: str, lang: str) -> float:
    n = norm_hi if lang == "hi" else norm_en
    t, x = n(term).replace(" ", ""), n(text).replace(" ", "")
    if not t:
        return 1.0
    if t in x:
        return 1.0
    best = 0.0
    for i in range(0, max(1, len(x) - len(t) + 1)):
        best = max(best, difflib.SequenceMatcher(None, t, x[i:i + len(t) + 2]).ratio())
    return best


def cer(ref: str, hyp: str, lang: str) -> float:
    n = norm_hi if lang == "hi" else norm_en
    r, h = n(ref).replace(" ", ""), n(hyp).replace(" ", "")
    import jiwer
    return jiwer.cer(r, h) if r else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True); ap.add_argument("--ref", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--variants", default="v2,v3,hi"); ap.add_argument("--seeds", default="11")
    ap.add_argument("--lines", default=""); ap.add_argument("--asr", default="large-v3-turbo"); ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--device", default="cpu"); ap.add_argument("--rescore", action="store_true")
    ap.add_argument("--temperature", type=float, default=0.8); ap.add_argument("--cfg", type=float, default=0.5); ap.add_argument("--exaggeration", type=float, default=0.5)
    a = ap.parse_args()
    import torch, torchaudio as ta, numpy as np, librosa
    torch.set_num_threads(a.threads)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    lines = json.loads((HERE / "testset.json").read_text())["lines"]
    if a.lines:
        keep = set(a.lines.split(",")); lines = [l for l in lines if l["id"] in keep]
    seeds = [int(s) for s in a.seeds.split(",")]
    res_p = out / "results.json"; res = json.loads(res_p.read_text()) if res_p.exists() else {"takes": []}
    done = {(t["variant"], t["line"], t["seed"]) for t in res["takes"]}
    for v in a.variants.split(","):
        todo = [(l, s) for l in lines for s in seeds if (v != "hi" or l["lang"] == "hi") and (v, l["id"], s) not in done]
        if not todo:
            continue
        t0 = time.time(); m = load_tts(Path(a.ckpt), *VARIANTS[v], device=a.device); print(f"[{v}] loaded in {time.time() - t0:.0f}s", flush=True)
        for l, s in todo:
            f = out / f"{v}_{l['id']}_s{s}.wav"
            torch.manual_seed(s); t0 = time.time()
            w = m.generate(l["say"], language_id=l["lang"], audio_prompt_path=a.ref, exaggeration=a.exaggeration, cfg_weight=a.cfg, temperature=a.temperature)
            ta.save(str(f), w.cpu(), m.sr)
            res["takes"].append({"variant": v, "line": l["id"], "seed": s, "file": f.name, "gen_s": round(time.time() - t0, 1),
                                 "dur_s": round(w.shape[-1] / m.sr, 2)})
            print(f"[{v}] {l['id']} s{s} {time.time() - t0:.0f}s", flush=True)
            res_p.write_text(json.dumps(res, ensure_ascii=False, indent=1))
        del m; import gc; gc.collect()
    # ---- score every take (independent ASR + speaker similarity)
    from faster_whisper import WhisperModel
    from chatterbox.models.voice_encoder import VoiceEncoder
    asr = WhisperModel(a.asr, device="cpu", compute_type="int8", cpu_threads=a.threads)
    ve = VoiceEncoder(); ve.load_state_dict(torch.load(Path(a.ckpt) / "ve.pt", map_location="cpu", weights_only=True)); ve.eval()
    ref16, _ = librosa.load(a.ref, sr=16000)
    ref_emb = ve.embeds_from_wavs([ref16], sample_rate=16000)[0]
    by = {l["id"]: l for l in json.loads((HERE / "testset.json").read_text())["lines"]}
    for t in res["takes"]:
        if "cer" in t and "transcript_en" in t and not a.rescore:
            continue
        l = by[t["line"]]; f = out / t["file"]
        w16, _ = librosa.load(str(f), sr=16000)
        segs, _ = asr.transcribe(w16, language=l["lang"], beam_size=5, vad_filter=False, condition_on_previous_text=False)
        hyp = " ".join(s.text.strip() for s in segs)
        if l["lang"] == "hi":       # English mode prints the English terms in Latin letters, so each term can be checked as written
            segs, _ = asr.transcribe(w16, language="en", beam_size=5, vad_filter=False, condition_on_previous_text=False)
            hyp_en = " ".join(s.text.strip() for s in segs)
        else:
            hyp_en = hyp
        hits = {term: round(term_hit(term, hyp_en, "en"), 2) for term in l.get("check", l["terms"])}
        emb = ve.embeds_from_wavs([w16], sample_rate=16000)[0]
        t.update(transcript=hyp, transcript_en=hyp_en, cer=round(cer(l["say"], hyp, l["lang"]), 3), terms=hits,
                 term_acc=round(sum(1 for x in hits.values() if x >= 0.8) / len(hits), 3),
                 spk_sim=round(float(np.dot(emb, ref_emb) / (np.linalg.norm(emb) * np.linalg.norm(ref_emb))), 3))
        print(f"{t['variant']:6s} {t['line']} s{t['seed']} cer {t['cer']:.3f} terms {t['term_acc']:.2f} sim {t['spk_sim']:.3f}", flush=True)
        res_p.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    # ---- summary per variant and language
    summ = {}
    for t in res["takes"]:
        k = f"{t['variant']}/{by[t['line']]['lang']}"
        d = summ.setdefault(k, {"n": 0, "cer": 0, "term_acc": 0, "spk_sim": 0, "rtf": 0})
        d["n"] += 1; d["cer"] += t["cer"]; d["term_acc"] += t["term_acc"]; d["spk_sim"] += t["spk_sim"]; d["rtf"] += t["gen_s"] / max(t["dur_s"], 0.1)
    for d in summ.values():
        for k in ("cer", "term_acc", "spk_sim", "rtf"):
            d[k] = round(d[k] / d["n"], 3)
    res["summary"] = summ; res_p.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(json.dumps(summ, indent=1))


if __name__ == "__main__":
    main()

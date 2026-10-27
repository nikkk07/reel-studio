"""Narration: Chatterbox multilingual (cloned reference voice), best-of-N takes, Whisper + UTMOS scoring, phrase alignment.
The model (V2, V3 or the V3 Hindi pack) is chosen per language in Studio Settings; see MODELS.

Runs in its own process so the models' memory is released afterwards:
    python -m reelworker.media.voice <job_dir>      (reads voice_in.json, writes voice_out.json)
"""
import gc, json, sys, time
from pathlib import Path
import numpy as np, soundfile as sf


def pick_device(pref: str):
    import torch
    if pref in ("mps", "cpu"):
        return pref
    return "mps" if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available() else "cpu"


# Voice models (all Chatterbox, MIT licence, run locally). name -> (Hugging Face repo of the text model, text model, speech decoder)
#   v2 - Chatterbox Multilingual V2 (the original studio voice)
#   v3 - Chatterbox Multilingual V3 (June 2026): more training data, better voice match and fewer slips
#   hi - V3 Hindi Language Pack: a Hindi-only finetune of V3 (best for the Hinglish accounts)
MODELS = {
    "v2": ("ResembleAI/chatterbox", "t3_mtl23ls_v2.safetensors", "s3gen.pt"),
    "v3": ("ResembleAI/chatterbox", "t3_mtl23ls_v3.safetensors", "s3gen_v3.safetensors"),
    "hi": ("ResembleAI/Chatterbox-Multilingual-hi", "t3_hi.safetensors", "s3gen_v3.safetensors"),
}
BASE_REPO = "ResembleAI/chatterbox"
BASE_FILES = ["ve.pt", "grapheme_mtl_merged_expanded_v1.json", "conds.pt"]


def model_files(name: str) -> dict:
    """Local paths of every file the model needs (downloaded once from Hugging Face, then cached)."""
    from huggingface_hub import hf_hub_download
    repo, t3, s3 = MODELS[name]
    files = {f: hf_hub_download(BASE_REPO, f) for f in BASE_FILES}
    files["t3"] = hf_hub_download(repo, t3)
    files["s3gen"] = hf_hub_download(BASE_REPO, s3)
    return files


def build_model(files: dict, device: str):
    """ChatterboxMultilingualTTS from explicit files (the library's own loader always pairs a text model with the V2 decoder)."""
    import torch
    from safetensors.torch import load_file
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS, Conditionals
    from chatterbox.models.t3 import T3
    from chatterbox.models.t3.modules.t3_config import T3Config
    from chatterbox.models.s3gen import S3Gen
    from chatterbox.models.tokenizers import MTLTokenizer
    from chatterbox.models.voice_encoder import VoiceEncoder
    cpu = torch.device("cpu")
    ve = VoiceEncoder(); ve.load_state_dict(torch.load(files["ve.pt"], map_location=cpu, weights_only=True)); ve.to(device).eval()
    t3 = T3(T3Config.multilingual()); st = load_file(files["t3"])
    if "model" in st:
        st = st["model"][0]
    t3.load_state_dict(st); t3.to(device).eval()
    s3 = S3Gen(); p = str(files["s3gen"])
    sd = load_file(p) if p.endswith(".safetensors") else torch.load(p, map_location=cpu, weights_only=True)
    miss, extra = s3.load_state_dict(sd, strict=False)         # .safetensors leaves out two fixed buffers (mel filters, window)
    bad = [k for k in miss if k not in ("tokenizer._mel_filters", "tokenizer.window")] + list(extra)
    if bad:
        raise RuntimeError(f"speech decoder does not fit the model: {bad[:4]}")
    s3.to(device).eval()
    tok = MTLTokenizer(str(files["grapheme_mtl_merged_expanded_v1.json"]))
    conds = Conditionals.load(files["conds.pt"], map_location=cpu).to(device)
    return ChatterboxMultilingualTTS(t3, s3, ve, tok, device, conds=conds)


def load_model(name: str, device: str, log=print):
    if name not in MODELS:
        log(f"unknown voice model '{name}', using v2"); name = "v2"
    try:
        files = model_files(name)
    except Exception as e:  # noqa: BLE001  (offline / download failed: the original model is always cached)
        if name == "v2":
            raise
        log(f"voice model {name} not available ({str(e)[:120]}); using v2"); name = "v2"; files = model_files(name)
    return build_model(files, device), name


def generate(job: Path, cfg: dict, log):
    import torch, torchaudio as ta
    dev = pick_device(cfg.get("device", "auto")); want = cfg.get("model", "v2")
    try:
        m, used = load_model(want, dev, log)
    except Exception as e:  # noqa: BLE001
        log(f"voice model on {dev} failed ({e}); falling back to cpu"); dev = "cpu"
        m, used = load_model(want, "cpu", log)
    log(f"voice model {used} on {dev}"); cfg["model_used"] = used
    out = {}
    for g in cfg["groups"]:
        files = []
        for s in cfg["seeds"]:
            f = job / "vo" / f"{g['id']}_s{s}.wav"
            if not f.exists():
                torch.manual_seed(s); t0 = time.time()
                kw = dict(language_id=cfg.get("tts_lang", "hi"), audio_prompt_path=cfg["ref"], exaggeration=float(cfg.get("exaggeration", 0.5)),
                          cfg_weight=float(cfg.get("cfg_weight", 0.5)), temperature=float(cfg.get("temperature", 0.8)))
                try:
                    w = m.generate(g["text"], **kw)
                except Exception as e:  # noqa: BLE001  (an op unsupported on Apple GPU) -> finish on CPU
                    if dev == "cpu": raise
                    log(f"generation on {dev} failed ({e}); switching to cpu")
                    del m; gc.collect(); dev = "cpu"; m, used = load_model(used, "cpu", log)
                    torch.manual_seed(s); w = m.generate(g["text"], **kw)
                ta.save(str(f), w.cpu(), m.sr); log(f"take {f.name} {time.time() - t0:.0f}s")
            files.append(str(f))
        out[g["id"]] = files
    del m; gc.collect()
    return out


def phrase_spans(words, phrases_say):
    """words: [(start, end, text)]; boundaries placed at the inter-word gap nearest the expected character position."""
    ch = [max(1, len(p)) for p in phrases_say]; T = sum(ch); n = len(words)
    if n == 0:
        return [[0.0, 0.1]] * len(ch)
    if len(ch) == 1 or n < len(ch):
        span = (words[-1][1] - words[0][0]) / len(ch)
        return [[words[0][0] + i * span, words[0][0] + (i + 1) * span] for i in range(len(ch))]
    total = words[-1][1] - words[0][0]; bidx = []; lo = 1
    for k in range(1, len(ch)):
        exp = words[0][0] + total * sum(ch[:k]) / T
        cand = [i for i in range(lo, n) if abs(words[i][0] - exp) < 0.9 and n - i >= len(ch) - k]
        if not cand:
            cand = [min(range(lo, n - (len(ch) - k) + 1), key=lambda i: abs(words[i][0] - exp))]
        i = max(cand, key=lambda i: (words[i][0] - words[i - 1][1]) - 0.15 * abs(words[i][0] - exp))
        bidx.append(i); lo = i + 1
    edges = [0] + bidx + [n]
    return [[words[edges[k]][0], words[edges[k + 1] - 1][1]] for k in range(len(ch))]


def _norm(s: str) -> str:
    import re, unicodedata
    s = unicodedata.normalize("NFC", s).replace("\u093c", "")          # फ़ = फ (Whisper is inconsistent with the nukta)
    s = re.sub("[\u0901\u0902]", "\u0902", s.lower())
    return re.sub(r"[^\u0900-\u097fa-z0-9]", "", s)


def text_match(expected: str, heard: str) -> float:
    """0-1: how closely the transcript follows the text the voice was given (spaces and punctuation ignored)."""
    import difflib
    a, b = _norm(expected), _norm(heard)
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() if a else 1.0


def score_and_align(job: Path, cfg: dict, takes: dict, log):
    import librosa, torch
    from faster_whisper import WhisperModel
    utmos = None
    try:
        from speechmos.utmos22.strong.model import UTMOS22Strong
        utmos = UTMOS22Strong(); utmos.load_state_dict(torch.load(cfg["utmos_ckpt"], map_location="cpu")); utmos.eval()
    except Exception as e:  # noqa: BLE001
        log(f"UTMOS unavailable ({e}); scoring by clarity only")
    asr = WhisperModel(cfg.get("whisper", "medium"), device="cpu", compute_type="int8", cpu_threads=cfg.get("threads", 4))
    result = {}
    for g in cfg["groups"]:
        expected_words = len(g["text"].split()); best = None; report = []
        for f in takes[g["id"]]:
            a, sr = sf.read(f, dtype="float32"); a = a if a.ndim == 1 else a.mean(1)
            a16 = librosa.resample(a, orig_sr=sr, target_sr=16000)
            segs, _ = asr.transcribe(a16, beam_size=5, language=cfg.get("asr_lang", "hi"), word_timestamps=True)
            words = [(w.start, w.end, w.word.strip(), w.probability) for s in segs for w in s.words]
            wprob = float(np.mean([w[3] for w in words])) if words else 0.0
            cover = len(words) / max(1, expected_words)
            u = float(utmos(torch.from_numpy(a16).unsqueeze(0), 16000).item()) if utmos else 4.0
            match = text_match(g["text"], " ".join(w[2] for w in words))     # did it say the words it was given?
            sc = (1 - wprob) * 3 + abs(1 - min(cover, 1.3)) * 2 + max(0.0, 4.5 - u) * 0.5 + (1 - match) * 3
            _, (i0, i1) = librosa.effects.trim(a, top_db=38)
            s0 = max(0.0, min(i0 / sr, words[0][0] if words else 0) - 0.04)
            e0 = min(len(a) / sr, max(i1 / sr, words[-1][1] if words else len(a) / sr) + 0.06)
            spans = phrase_spans([(w[0], w[1], w[2]) for w in words], g["phrases_say"])
            rec = {"file": f, "score": round(sc, 3), "word_prob": round(wprob, 3), "coverage": round(cover, 2), "utmos": round(u, 2), "match": round(match, 3),
                   "transcript": " ".join(w[2] for w in words), "weak_words": [w[2] for w in words if w[3] < 0.4],
                   "text": g["text"], "trim": [round(s0, 3), round(e0, 3)], "spans": [[round(x, 3), round(y, 3)] for x, y in spans]}
            report.append(rec)
            if best is None or sc < best["score"]:
                best = rec
        result[g["id"]] = {"file": best["file"], "trim": best["trim"], "spans": best["spans"], "chosen": best, "takes": report}
        log(f"{g['id']}: chose {Path(best['file']).name} prob {best['word_prob']} match {best.get('match')} utmos {best['utmos']}")
    return result


def main(job_dir: str, cfg_name: str = "voice_in.json", out_name: str = "voice_out.json"):
    job = Path(job_dir); (job / "vo").mkdir(exist_ok=True)
    cfg = json.loads((job / cfg_name).read_text(encoding="utf-8"))
    logf = open(job / "voice.log", "a", encoding="utf-8")
    def log(m):
        logf.write(f"{time.strftime('%H:%M:%S')} {m}\n"); logf.flush(); print(m, flush=True)
    takes = generate(job, cfg, log)
    res = score_and_align(job, cfg, takes, log)
    (job / out_name).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(*sys.argv[1:4])

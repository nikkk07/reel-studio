"""Narration: Chatterbox multilingual (cloned reference voice), best-of-N takes, Whisper + UTMOS scoring, phrase alignment.

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


def generate(job: Path, cfg: dict, log):
    import torch, torchaudio as ta
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS
    dev = pick_device(cfg.get("device", "auto"))
    try:
        m = ChatterboxMultilingualTTS.from_pretrained(device=dev)
    except Exception as e:  # noqa: BLE001
        log(f"voice model on {dev} failed ({e}); falling back to cpu"); dev = "cpu"
        m = ChatterboxMultilingualTTS.from_pretrained(device="cpu")
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
                    del m; gc.collect(); dev = "cpu"; m = ChatterboxMultilingualTTS.from_pretrained(device="cpu")
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
            sc = (1 - wprob) * 3 + abs(1 - min(cover, 1.3)) * 2 + max(0.0, 4.5 - u) * 0.5
            _, (i0, i1) = librosa.effects.trim(a, top_db=38)
            s0 = max(0.0, min(i0 / sr, words[0][0] if words else 0) - 0.04)
            e0 = min(len(a) / sr, max(i1 / sr, words[-1][1] if words else len(a) / sr) + 0.06)
            spans = phrase_spans([(w[0], w[1], w[2]) for w in words], g["phrases_say"])
            rec = {"file": f, "score": round(sc, 3), "word_prob": round(wprob, 3), "coverage": round(cover, 2), "utmos": round(u, 2),
                   "transcript": " ".join(w[2] for w in words), "weak_words": [w[2] for w in words if w[3] < 0.4],
                   "text": g["text"], "trim": [round(s0, 3), round(e0, 3)], "spans": [[round(x, 3), round(y, 3)] for x, y in spans]}
            report.append(rec)
            if best is None or sc < best["score"]:
                best = rec
        result[g["id"]] = {"file": best["file"], "trim": best["trim"], "spans": best["spans"], "chosen": best, "takes": report}
        log(f"{g['id']}: chose {Path(rec['file']).name} prob {rec['word_prob']} utmos {rec['utmos']}")
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

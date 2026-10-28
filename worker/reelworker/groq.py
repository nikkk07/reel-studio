"""Groq (free tier) as the studio's backup brain and second pair of ears.

* chat(): answers a text-only agent call (no web, no images) as JSON matching the call's schema. Used ONLY when Claude
  is limited or not signed in, and only when the whole request fits the free tier (8,000 tokens a minute); anything
  bigger, or anything that needs web research or looking at images, keeps waiting for Claude.
* transcribe(): Whisper large-v3 on Groq, a stronger second transcript of each voice take for the Voice Inspector.
Docs: https://console.groq.com/docs/rate-limits , https://console.groq.com/docs/speech-to-text
The key is read from Studio Settings (cloud) or the Mac's .env; it is never logged."""
import json, re
from pathlib import Path
import httpx
from . import config, studio

API = config.get("GROQ_API", "https://api.groq.com/openai/v1")
MODELS = ("openai/gpt-oss-120b", "openai/gpt-oss-20b")         # bigger first; the smaller one has its own free limits
TPM_LIMIT = 8000                                               # free tier tokens per minute (per model)


class GroqUnavailable(Exception):
    pass


def key():
    return studio.get("GROQ_API_KEY")


def estimate_tokens(text: str) -> int:
    """Conservative: ~3 characters a token (Hindi/Hinglish and JSON tokenize worse than English prose)."""
    return len(text) // 3 + 1


def fits(text: str, schema: dict, out_tokens: int) -> bool:
    return estimate_tokens(text) + estimate_tokens(json.dumps(schema)) + out_tokens <= TPM_LIMIT


def _missing(obj, schema, path="") -> list:
    """Tiny JSON-schema check: required keys and basic types (the agents' own contract checks run after this)."""
    out = []
    t = schema.get("type")
    if t == "object":
        if not isinstance(obj, dict):
            return [f"{path or 'answer'} must be an object"]
        for k in schema.get("required", []):
            if k not in obj:
                out.append(f"missing {path}{k}")
        for k, sub in (schema.get("properties") or {}).items():
            if k in obj:
                out += _missing(obj[k], sub, f"{path}{k}.")
    elif t == "array":
        if not isinstance(obj, list):
            return [f"{path[:-1]} must be a list"]
        if "minItems" in schema and len(obj) < schema["minItems"]:
            out.append(f"{path[:-1]} needs at least {schema['minItems']} items")
        for i, it in enumerate(obj[:50]):
            out += _missing(it, schema.get("items") or {}, f"{path[:-1]}[{i}].")
    elif t in ("string", "integer", "number", "boolean") and obj is not None:
        ok = {"string": isinstance(obj, str), "integer": isinstance(obj, int) and not isinstance(obj, bool),
              "number": isinstance(obj, (int, float)) and not isinstance(obj, bool), "boolean": isinstance(obj, bool)}[t]
        if not ok:
            out.append(f"{path[:-1]} must be a {t}")
        if t == "string" and schema.get("enum") and obj not in schema["enum"]:
            out.append(f"{path[:-1]} must be one of {schema['enum']}")
    return out


def chat(name: str, text: str, schema: dict, out_tokens: int = 3000, timeout: int = 120) -> dict:
    k = key()
    if not k:
        raise GroqUnavailable("no Groq key saved")
    if not fits(text, schema, out_tokens):
        raise GroqUnavailable(f"{name} is too big for Groq's free tier ({estimate_tokens(text)} tokens)")
    sys_msg = ("You are one agent of a short-form video studio. Answer with ONE JSON object only (no prose, no code fences) that "
               "matches this JSON schema exactly:\n" + json.dumps(schema, ensure_ascii=False))
    last = None
    for model in MODELS:
        msgs = [{"role": "system", "content": sys_msg}, {"role": "user", "content": text}]
        for attempt in range(2):
            try:
                r = httpx.post(f"{API}/chat/completions", headers={"Authorization": f"Bearer {k}"}, timeout=timeout,
                               json={"model": model, "messages": msgs, "temperature": 0.4, "max_completion_tokens": out_tokens,
                                     "response_format": {"type": "json_object"}})
            except httpx.HTTPError as e:
                last = f"network ({type(e).__name__})"; break
            if r.status_code in (401, 403):
                raise GroqUnavailable("Groq did not accept the key")
            if r.status_code in (413, 429, 498, 503):           # too large / free limit / capacity: try the other model
                last = f"HTTP {r.status_code}"; break
            if r.status_code >= 400:
                last = f"HTTP {r.status_code}: {r.text[:160]}"; break
            content = (r.json().get("choices") or [{}])[0].get("message", {}).get("content") or ""
            m = re.search(r"\{.*\}", content, re.S)
            try:
                obj = json.loads(m.group(0)) if m else None
            except json.JSONDecodeError:
                obj = None
            problems = _missing(obj, schema) if obj is not None else ["the answer was not valid JSON"]
            if not problems:
                return obj
            last = "; ".join(problems[:3])
            msgs += [{"role": "assistant", "content": content[:6000]},
                     {"role": "user", "content": "Fix these problems and answer again with the full JSON object only: " + last}]
    raise GroqUnavailable(f"Groq could not answer {name}: {last}")


def transcribe(path: Path, lang: str = "hi", timeout: int = 60) -> str:
    """Whisper large-v3 transcript of one short voice take ('' if Groq is not set up or fails)."""
    k = key()
    if not k:
        return ""
    try:
        with open(path, "rb") as fh:
            r = httpx.post(f"{API}/audio/transcriptions", headers={"Authorization": f"Bearer {k}"}, timeout=timeout,
                           data={"model": "whisper-large-v3", "language": lang, "response_format": "json", "temperature": "0"},
                           files={"file": (Path(path).name, fh, "audio/wav")})
        return (r.json().get("text") or "").strip() if r.status_code == 200 else ""
    except Exception:  # noqa: BLE001
        return ""

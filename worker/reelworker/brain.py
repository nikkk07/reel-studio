"""Claude as the decision-maker, called through Claude Code's non-interactive mode with the team's Pro login.

Every call: JSON-schema output, least-privilege tools, an empty per-call working directory.
Docs: https://code.claude.com/docs/en/headless
"""
import json, os, re, subprocess, threading, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from . import config, procs
from .runner import QuotaError, TransientError

PROMPTS = Path(__file__).parent / "prompts"
FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures"
_LIMIT_RE = re.compile(r"(usage limit|rate limit|limit reached|out of (extra )?usage|5-hour limit|weekly limit|session limit|hit your (\w+ )?limit)", re.I)


def prompt(name: str, **vars) -> str:
    text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    for k, v in vars.items():
        text = text.replace("{{" + k + "}}", v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, indent=1))
    return text


def _env():
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)        # never bill the API by accident: use the subscription login
    return env


def _reset_time(text: str):
    m = re.search(r"reset[s]?\s*(?:at)?\s*(\d{1,2})(?::(\d{2}))?\s*([ap]m)?", text or "", re.I)
    now = datetime.now().astimezone()
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
        if ap == "pm" and h < 12: h += 12
        if ap == "am" and h == 12: h = 0
        t = now.replace(hour=h % 24, minute=mi, second=0, microsecond=0)
        if t <= now: t += timedelta(days=1)
        return (t + timedelta(minutes=2)).astimezone(timezone.utc).isoformat()
    return (now + timedelta(hours=1)).astimezone(timezone.utc).isoformat()


def _describe(tool: str, inp: dict) -> str:
    """Plain-language line for the website's live log."""
    if tool == "WebSearch":
        return f"Searching the web: {str(inp.get('query', ''))[:90]}"
    if tool == "WebFetch":
        url = str(inp.get("url", "")); host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
        return f"Reading {host}"
    if tool == "Read":
        f = Path(str(inp.get("file_path", ""))).name
        return {"frames.jpg": "Watching the finished reel frame by frame", "report.json": "Checking loudness, timing and captions"}.get(
            f, f"Looking at {f.replace('sheet_', 'clip options for ').replace('.jpg', '')}")
    return f"Using {tool}"


def ask(name: str, text: str, schema: dict, tools: list, cwd: Path, timeout: int = 1200, on_event=None) -> dict:
    """Run one Claude call and return the schema-validated object. on_event(str) receives live tool activity."""
    if config.BRAIN_MODE == "mock":
        delay = config.get("REELSTUDIO_MOCK_BRAIN_SECONDS", "")
        if delay:   # tests: behave like a long Claude call (a real child process inside the job folder)
            cwd.mkdir(parents=True, exist_ok=True)
            if subprocess.run(["sleep", delay], cwd=cwd).returncode != 0:
                raise TransientError(f"Claude call {name} was interrupted")
        return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    cwd.mkdir(parents=True, exist_ok=True)
    cmd = [config.CLAUDE_BIN, "-p", text, "--output-format", "stream-json", "--verbose", "--json-schema", json.dumps(schema),
           "--permission-mode", "dontAsk"] + (["--allowedTools", ",".join(tools)] if tools else [])
    if config.CLAUDE_MODEL:
        cmd += ["--model", config.CLAUDE_MODEL]
    if _supports_permission_prompts():
        cmd += ["--permission-prompts", "none"]
    t0 = time.time(); lines = []; res = None
    errf = cwd / f"claude_{name}_{int(t0)}.stderr"
    errh = open(errf, "w")
    p = procs.track(subprocess.Popen(cmd, cwd=cwd, env=_env(), stdout=subprocess.PIPE, stderr=errh, text=True), cwd)
    killer = threading.Timer(timeout, p.kill); killer.start()
    try:
        for line in p.stdout:
            lines.append(line)
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "result":
                res = ev
            elif ev.get("type") == "assistant" and on_event:
                for blk in (ev.get("message") or {}).get("content") or []:
                    if blk.get("type") == "tool_use":
                        try:
                            on_event(_describe(blk.get("name", ""), blk.get("input") or {}))
                        except Exception:  # noqa: BLE001
                            pass
        p.wait()
    finally:
        killer.cancel()
    errh.close(); err = errf.read_text(errors="ignore") if errf.exists() else ""
    if time.time() - t0 >= timeout - 1:
        raise TransientError(f"Claude timed out after {timeout}s ({name})")
    if True:   # raw transcript for troubleshooting (no secrets in it)
        (cwd / f"claude_{name}_{int(t0)}.jsonl").write_text("".join(lines)[:4_000_000] or err[-5000:], encoding="utf-8")
    if res is None:
        res = {"is_error": True, "result": (lines[-1] if lines else "") or err[-2000:]}
    msg = str(res.get("result") or err or "")
    if res.get("is_error") or p.returncode != 0:
        if _LIMIT_RE.search(msg):
            raise QuotaError(msg[:300], _reset_time(msg))
        if re.search(r"(login|log in|authenticat|credential)", msg, re.I):
            raise RuntimeError(f"Claude Code is not signed in for this Mac user. Run `claude` in Terminal and sign in. (Claude said: {msg.strip()[:160]})")
        raise TransientError(f"Claude call {name} failed (exit {p.returncode}): {msg.strip()[:300] or 'no output'}")
    obj = res.get("structured_output")
    if obj is None:
        m = re.search(r"\{.*\}", msg, re.S)
        if not m:
            raise TransientError(f"Claude call {name} returned no structured output")
        obj = json.loads(m.group(0))
    return obj


_PP = None
def _supports_permission_prompts():
    global _PP
    if _PP is None:
        try:
            v = subprocess.run([config.CLAUDE_BIN, "--version"], capture_output=True, text=True, timeout=30).stdout
            nums = [int(x) for x in re.findall(r"(\d+)\.(\d+)\.(\d+)", v)[0]]
            _PP = tuple(nums) >= (2, 1, 259)
        except Exception:  # noqa: BLE001
            _PP = False
    return _PP


def health_check():
    """(ok, note). Uses one tiny call at most every 30 minutes (called by the heartbeat thread)."""
    if config.BRAIN_MODE == "mock":
        return True, "mock brain"
    try:
        v = subprocess.run([config.CLAUDE_BIN, "--version"], capture_output=True, text=True, timeout=30)
        if v.returncode != 0:
            return False, "Claude Code not installed"
        d = config.APP / "state" / "health"; d.mkdir(parents=True, exist_ok=True)
        r = ask("health", "Reply with the single word OK.", {"type": "object", "properties": {"reply": {"type": "string"}},
                "required": ["reply"]}, [], d, timeout=120)
        return ("OK" in r.get("reply", "").upper()), v.stdout.strip()[:60]
    except QuotaError as e:
        return False, f"usage limit until {e.reset_at}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200]

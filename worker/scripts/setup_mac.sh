#!/bin/bash
# Reel Studio – one-time setup (and later updates) on the Mac mini.
#   bash setup_mac.sh            full setup
#   bash setup_mac.sh --update   copy new code/library, reinstall packages if changed, restart the service
# Secrets are typed in by you (hidden) and stored only in ~/ReelStudio/.env (permissions 600). Nothing is printed.
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"                 # .../EduReelStudio/reelstudio/worker
EDU="$(cd "$SRC/../.." && pwd)"                          # .../EduReelStudio
RS="$HOME/ReelStudio"
LABEL="com.weoneaviation.reelstudio"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UPDATE=0; [ "${1:-}" = "--update" ] && UPDATE=1
say() { printf "\n\033[1;33m==> %s\033[0m\n" "$*"; }

[ "$(uname -s)" = "Darwin" ] || { echo "Run this on the Mac (macOS)."; exit 1; }
if ! command -v brew >/dev/null 2>&1; then
  [ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)" || {
    echo "Homebrew is needed. Install it from https://brew.sh (one command), then run this script again."; exit 1; }
fi

say "System packages (python 3.11, ffmpeg)"
brew list python@3.11 >/dev/null 2>&1 || brew install python@3.11
brew list ffmpeg >/dev/null 2>&1 || brew install ffmpeg
PY="$(brew --prefix python@3.11)/bin/python3.11"

say "Folders in $RS"
mkdir -p "$RS"/{app,library/voice/refs,library/brand,library/models,jobs,logs,state,deliverables}

say "Code"
rsync -a --delete --exclude "__pycache__" "$SRC/reelworker" "$SRC/tests" "$SRC/requirements-mac.txt" "$RS/app/"

say "Library (fonts, voice references, pronunciation dictionary, quality model, logo)"
LIB="$EDU/library"
rsync -a "$LIB/fonts/" "$RS/library/fonts/"
cp -p "$LIB/voice/pronunciation_lexicon.json" "$RS/library/voice/"
[ -d "$LIB/voice/refs" ] && rsync -a "$LIB/voice/refs/" "$RS/library/voice/refs/"
[ -f "$RS/library/voice/refs/male.wav" ] || { [ -f "$LIB/voice/nikhil_clean/refFast.wav" ] && cp -p "$LIB/voice/nikhil_clean/refFast.wav" "$RS/library/voice/refs/male.wav"; } || true
[ -f "$LIB/brand/logo.png" ] && cp -p "$LIB/brand/logo.png" "$RS/library/brand/logo.png"
CK="utmos22_strong_step7459_v1.pt"
if [ ! -f "$RS/library/models/$CK" ] || [ "$(stat -f%z "$RS/library/models/$CK")" != "$(stat -f%z "$LIB/models/$CK")" ]; then cp -p "$LIB/models/$CK" "$RS/library/models/"; fi
ln -sfn "$RS/deliverables" "$EDU/ReelStudio_deliverables"

if [ ! -f "$RS/.env" ]; then
  say "Settings (.env) – values are hidden while typing"
  umask 077; : > "$RS/.env"
  # copy only the two stock-footage keys from the existing EduReelStudio/.env (nothing else, nothing printed)
  grep -E '^(PEXELS_API_KEY|PIXABAY_API_KEY)=' "$EDU/.env" >> "$RS/.env" 2>/dev/null || true
  read -r -p "Supabase Project URL (https://xxxx.supabase.co): " SBURL
  read -r -s -p "Supabase service_role key (hidden): " SBKEY; echo
  printf 'SUPABASE_URL=%s\nSUPABASE_SERVICE_ROLE_KEY=%s\nREELSTUDIO_WORKER_ID=mac-mini\n' "$SBURL" "$SBKEY" >> "$RS/.env"
  unset SBKEY; chmod 600 "$RS/.env"
fi

say "Python environment (first run downloads ~2 GB)"
[ -x "$RS/.venv/bin/python" ] || "$PY" -m venv "$RS/.venv"
REQ_HASH="$(shasum "$RS/app/requirements-mac.txt" | cut -d' ' -f1)"
if [ "$(cat "$RS/state/req.hash" 2>/dev/null || true)" != "$REQ_HASH" ]; then
  "$RS/.venv/bin/pip" install -q --upgrade pip wheel
  "$RS/.venv/bin/pip" install -r "$RS/app/requirements-mac.txt"
  echo "$REQ_HASH" > "$RS/state/req.hash"
fi

if ! command -v claude >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/claude" ]; then
  say "Claude Code (uses your Claude Pro login, no API key)"
  curl -fsSL https://claude.ai/install.sh | bash
fi
export PATH="$HOME/.local/bin:$PATH"
if [ $UPDATE -eq 0 ]; then
  say "Sign in to Claude Code once: a browser window opens; choose your Claude Pro account, then type /exit"
  read -r -p "Press Enter to open Claude Code, or type s and Enter if you are already signed in: " ans || true
  if [ "${ans:-}" != "s" ]; then (cd "$RS" && unset ANTHROPIC_API_KEY && claude) || true; fi
fi

say "Self-test"
cd "$RS/app"
export REELSTUDIO_ROOT="$RS"
if [ $UPDATE -eq 0 ]; then "$RS/.venv/bin/python" -m reelworker.selftest --render --voice || { echo "Fix the FAIL lines above, then run this script again."; exit 1; }
else "$RS/.venv/bin/python" -m reelworker.selftest || true; fi

say "Background service (starts at login, restarts itself, keeps the Mac awake while running)"
mkdir -p "$HOME/Library/LaunchAgents"
sed "s#__HOME__#$HOME#g" "$SRC/scripts/$LABEL.plist" > "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "gui/$(id -u)/$LABEL"
sleep 5; tail -n 5 "$RS/logs/worker.log" 2>/dev/null || true

cat <<'TXT'

Done. Recommended once in System Settings so the server stays reachable 24x7:
  • Energy (or Battery → Options): "Prevent automatic sleeping when the display is off" = ON,
    "Start up automatically after a power failure" = ON, "Wake for network access" = ON
  • Users & Groups: automatic login for this user (the service runs inside your login session)
Useful commands:
  tail -f ~/ReelStudio/logs/worker.log                                   live log
  launchctl kickstart -k gui/$(id -u)/com.weoneaviation.reelstudio       restart
  launchctl bootout gui/$(id -u)/com.weoneaviation.reelstudio            stop
  bash setup_mac.sh --update                                             after new code arrives
TXT

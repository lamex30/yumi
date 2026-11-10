#!/bin/sh
# The real voice for Yumi: a local Silero TTS, no cloud.
#   ./tools/tts_setup.sh
# Puts torch + the voices into their own venv (~/.local/share/yumi/tts, ~450 MB);
# your system Python is not touched. Then in Yumi: Settings → Sound → Voice type → Real.
set -e
cd "$(dirname "$0")/.."
DIR="${YUMI_TTS_HOME:-$HOME/.local/share/yumi/tts}"

# torch has no wheels for the very newest Pythons yet: find one it likes (3.10–3.13)
PY=
for v in 3.13 3.12 3.11 3.10; do
    if command -v "python$v" >/dev/null 2>&1; then PY="python$v"; break; fi
done
if [ -z "$PY" ] && command -v python3 >/dev/null 2>&1 && \
   python3 -c 'import sys; sys.exit(0 if sys.version_info[:2] <= (3, 13) else 1)' 2>/dev/null; then
    PY=python3
fi
[ -n "$PY" ] || { echo "Need Python 3.10–3.13 for torch (newer ones have no wheels yet)." >&2; exit 1; }
echo "Using $PY; the venv goes to $DIR"

if [ -x "$DIR/bin/python" ] && "$DIR/bin/python" -c 'import torch, numpy, omegaconf' >/dev/null 2>&1; then
    echo "The voice venv is already in $DIR"
else
    if command -v uv >/dev/null 2>&1; then
        uv venv --python "$PY" "$DIR"
        uv pip install --python "$DIR/bin/python" torch --index-url https://download.pytorch.org/whl/cpu
        uv pip install --python "$DIR/bin/python" numpy omegaconf
    else
        "$PY" -m venv "$DIR"
        "$DIR/bin/pip" install --quiet --upgrade pip
        "$DIR/bin/pip" install --quiet torch --index-url https://download.pytorch.org/whl/cpu
        "$DIR/bin/pip" install --quiet numpy omegaconf
    fi
fi

echo 'Downloading the voices (~100 MB, once)...'
"$DIR/bin/python" yumi/tts_daemon.py --warmup

# Turn the real voice on; if she is running, restart her, so it takes effect at once.
YUMI=yumi
command -v yumi >/dev/null 2>&1 || YUMI=./bin/yumi
RUNNING=0
if pgrep -f 'moth_girl[.]py' >/dev/null 2>&1; then RUNNING=1; fi
if [ "$RUNNING" = 1 ]; then
    "$YUMI" --quit
    i=0; while [ $i -lt 50 ] && pgrep -f 'moth_girl[.]py' >/dev/null 2>&1; do sleep 0.1; i=$((i + 1)); done
fi
"$DIR/bin/python" - <<'PY'
import json
import os
p = os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config'), 'yumi', 'settings.json')
try:
    s = json.load(open(p))
except (OSError, ValueError):
    s = {}
if s.get('voice_kind') != 'tts':
    s['voice_kind'] = 'tts'
    tmp = p + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(s, f, indent=1, ensure_ascii=False)
    os.replace(tmp, p)
    print('Voice type: real')
PY
if [ "$RUNNING" = 1 ]; then "$YUMI"; fi

echo
echo 'Done: she speaks with her real voice (in her menu: Settings → Sound → Voice type).'
echo 'The speakers and the pitch live in ~/.config/yumi/tts.json (see yumi/tts_daemon.py);'
echo 'after editing it: pkill -f yumi/tts_daemon.py'

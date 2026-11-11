#!/bin/sh
# The ears for Yumi: a local Vosk speech recognition, no cloud.
#   ./tools/stt_setup.sh
# Puts vosk into its own venv (~/.local/share/yumi/stt, ~10 MB); your system Python is not
# touched. The small Russian model (~45 MB) downloads by itself the first time she listens.
set -e
cd "$(dirname "$0")/.."
DIR="${YUMI_STT_HOME:-$HOME/.local/share/yumi/stt}"

if [ -x "$DIR/bin/python" ] && "$DIR/bin/python" -c 'import vosk' >/dev/null 2>&1; then
    echo "The ears venv is already in $DIR"
else
    if command -v uv >/dev/null 2>&1; then
        uv venv --python python3 "$DIR"
        uv pip install --python "$DIR/bin/python" vosk
        uv pip install --python "$DIR/bin/python" webrtcvad 'setuptools<81' || \
            echo 'no webrtcvad (a C compiler is wanted): she hears on without the voice detector'
    else
        python3 -m venv "$DIR"
        "$DIR/bin/pip" install --quiet --upgrade pip 'setuptools<81'
        "$DIR/bin/pip" install --quiet vosk webrtcvad || \
            echo 'no webrtcvad (a C compiler is wanted): she hears on without the voice detector'
    fi
fi

echo 'Checking the microphone tool (parecord, pw-record or arecord)...'
if ! command -v parecord >/dev/null 2>&1 && ! command -v pw-record >/dev/null 2>&1 \
   && ! command -v arecord >/dev/null 2>&1; then
    echo 'None found: install pipewire-utils (parecord, pw-record) or alsa-utils (arecord).' >&2
    exit 1
fi

# an ears daemon from an older setup would keep answering with the old code
pkill -f 'yumi/stt_daemon[.]py' 2>/dev/null || true
rm -f "${XDG_CACHE_HOME:-$HOME/.cache}/yumi/stt/port.json" 2>/dev/null || true

# A test listen: downloads the Russian model at once and leaves the daemon warm.
"$DIR/bin/python" - <<'PY'
import sys, os
sys.path.insert(0, os.getcwd())
from yumi import ears
ears._ensure()
print('Listening once — say something...')
text = ears.listen('ru', secs=5)
print('She heard:', text if text else '(silence)')
PY

echo
echo 'Done: the mic button on her ring makes her listen and answer (through local Ollama).'
echo 'Her answer model lives in the settings: Settings → Her mind → Answer model.'

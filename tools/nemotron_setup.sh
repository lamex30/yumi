#!/bin/sh
# The optional ear for Yumi: NVIDIA Nemotron 3.5 ASR Streaming 0.6b (int8, ~650 MB),
# the same family of models the Handy dictation app listens with. ru and en are among
# its 40 locales; it is a streaming transducer, so phrases are read on a modest CPU.
#   ./tools/nemotron_setup.sh
# Puts sherpa-onnx into the ears venv (~/.local/share/yumi/stt) and the model next to
# it; your system Python is not touched. After this, set in ~/.config/yumi/settings.json:
#   "stt_engine": "nemotron"
# Whisper stays installed and takes over whenever this ear is missing or fails.
set -e
cd "$(dirname "$0")/.."
DIR="${YUMI_STT_HOME:-$HOME/.local/share/yumi/stt}"
NAME=sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11
URL=https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/$NAME.tar.bz2

if [ ! -x "$DIR/bin/python" ]; then
    echo "The ears venv is not in $DIR: run tools/stt_setup.sh first." >&2
    exit 1
fi

if "$DIR/bin/python" -c 'import sherpa_onnx' >/dev/null 2>&1; then
    echo 'sherpa-onnx is already in.'
else
    echo 'Installing sherpa-onnx (~60 MB of code, into the ears venv)...'
    if command -v uv >/dev/null 2>&1; then
        uv pip install --python "$DIR/bin/python" sherpa-onnx
    else
        "$DIR/bin/python" -m pip install --quiet sherpa-onnx
    fi
fi

if [ -f "$DIR/$NAME/encoder.int8.onnx" ]; then
    echo 'The Nemotron model is already in.'
else
    echo 'Downloading the model (~650 MB, once)...'
    tmp="$DIR/$NAME.tar.bz2.part"
    curl -fL --retry 3 -o "$tmp" "$URL"
    tar -xjf "$tmp" -C "$DIR"
    rm -f "$tmp"
fi
if [ ! -f "$DIR/$NAME/encoder.int8.onnx" ]; then
    echo 'The model did not unpack as expected.' >&2
    exit 1
fi
echo "The model is in $DIR/$NAME."

# an ears daemon from an older code keeps whispering: a restart picks the new ear up
pkill -f 'yumi/stt_daemon[.]py' 2>/dev/null || true
rm -f "${XDG_CACHE_HOME:-$HOME/.cache}/yumi/stt/port.json" 2>/dev/null || true

echo
echo 'Now set "stt_engine": "nemotron" in ~/.config/yumi/settings.json and restart Yumi.'
echo 'A test read (uses the model own sample):'
"$DIR/bin/python" - <<PY
import json, wave, sys, os
from pathlib import Path
import numpy as np
import sherpa_onnx
d = '$DIR/$NAME'
rec = sherpa_onnx.OnlineRecognizer.from_transducer(
    tokens=d + '/tokens.txt', encoder=d + '/encoder.int8.onnx',
    decoder=d + '/decoder.int8.onnx', joiner=d + '/joiner.int8.onnx',
    num_threads=1, sample_rate=16000, feature_dim=80, decoding_method='greedy_search')
for path in sorted(Path(d, 'test_wavs').glob('*.wav')):
    with wave.open(str(path)) as f:
        audio = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16).astype('float32') / 32768.0
    st = rec.create_stream()
    st.set_option('language', 'auto')
    st.accept_waveform(16000, audio)
    st.input_finished()
    while rec.is_ready(st): rec.decode_stream(st)
    try: text = json.loads(rec.get_result_as_json_string(st))['text']
    except Exception: text = str(rec.get_result(st))
    if text and text.strip():
        print('Heard:', text, f'({path.name})')
        break
PY

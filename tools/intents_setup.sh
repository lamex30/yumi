#!/bin/sh
# The tiny mind for Yumi's voice commands: a local classifier, no cloud («System One»).
#   ./tools/intents_setup.sh [--epochs N]
# Puts torch + transformers into their own venv (~/.local/share/yumi/intents-train,
# ~500 MB; your system Python is not touched), teaches rubert-tiny2 on the sayings
# (data/intents-corpus.json + the user's actions.json) and exports an int8 ONNX
# model (~30 MB) into ~/.local/share/yumi/intents/. Then it gives the running
# Yumi the two runtime bits she needs: onnxruntime and tokenizers (pip --user).
# Retrain any time actions.json grew: run this again.
set -e
cd "$(dirname "$0")/.."
DIR="${YUMI_INTENTS_HOME:-$HOME/.local/share/yumi/intents-train}"
OUT="${YUMI_INTENTS_OUT:-$HOME/.local/share/yumi/intents}"

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

if [ ! -x "$DIR/bin/python" ]; then
    if command -v uv >/dev/null 2>&1; then
        uv venv --python "$PY" "$DIR"
    else
        "$PY" -m venv "$DIR"
    fi
fi
if [ -x "$DIR/bin/python" ] && "$DIR/bin/python" -c 'import torch, transformers, onnx, onnxruntime' >/dev/null 2>&1; then
    echo "The training venv is already in $DIR"
else
    if command -v uv >/dev/null 2>&1; then
        uv pip install --python "$DIR/bin/python" torch --index-url https://download.pytorch.org/whl/cpu
        uv pip install --python "$DIR/bin/python" transformers onnxruntime onnx
    else
        "$DIR/bin/pip" install --quiet --upgrade pip
        "$DIR/bin/pip" install --quiet torch --index-url https://download.pytorch.org/whl/cpu
        "$DIR/bin/pip" install --quiet transformers onnxruntime onnx
    fi
fi

echo 'Teaching the tiny mind (rubert-tiny2, ~110 MB download once, a few minutes)...'
"$DIR/bin/python" tools/intents_train.py "$@" --out "$OUT"

# the runtime bits for the app's own Python (onnxruntime is light; tokenizers too)
echo
for PKG in onnxruntime tokenizers; do
    if python3 -c "import $PKG" >/dev/null 2>&1; then
        echo "$PKG: already here"
    else
        echo "$PKG: installing into the user site (pip install --user)..."
        python3 -m pip install --user --quiet "$PKG" || \
            echo "Could not install $PKG: run «python3 -m pip install --user $PKG» by hand." >&2
    fi
done

echo
echo "Done: the model lives in $OUT."
echo 'Yumi picks it up on her next start; in her menu: Settings → Mind → Intents.'

#!/bin/sh
# Yumi's tiny mind: download the ready model from the releases page, and only
# when there is none (or --train is given) teach one on the spot.
#   ./tools/intents_setup.sh [--train] [--epochs N]
# Puts torch + transformers into their own venv (~/.local/share/yumi/intents-train,
# ~500 MB; your system Python is not touched), teaches rubert-tiny2 on the sayings
# (data/intents-corpus.json + the user's actions.json) and exports an int8 ONNX
# model (~30 MB) into ~/.local/share/yumi/intents/. Then it gives the running
# Yumi the one runtime bit she needs: onnxruntime (pip --user).
# Retrain any time actions.json grew: run this again with --train.
set -e
cd "$(dirname "$0")/.."
DIR="${YUMI_INTENTS_HOME:-$HOME/.local/share/yumi/intents-train}"
OUT="${YUMI_INTENTS_OUT:-$HOME/.local/share/yumi/intents}"
TRAIN_ONLY=0
if [ "${1:-}" = "--train" ]; then TRAIN_ONLY=1; shift; fi
RELEASE_URL=https://github.com/lamex30/yumi/releases/latest/download/yumi-intents-model.tar.gz

mkdir -p "$OUT"

# the ready-made model from the releases page: no venv, no training, ~20 MB
if [ "$TRAIN_ONLY" = 0 ] && [ -f "$OUT/model.onnx" ] && [ -f "$OUT/labels.json" ]; then
    echo "The model is already in $OUT"
elif [ "$TRAIN_ONLY" = 0 ]; then
    echo "Looking for a ready-made model on the releases page..."
    TMP=$(mktemp -d)
    GOT=0
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL -o "$TMP/m.tgz" "$RELEASE_URL" && GOT=1
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$TMP/m.tgz" "$RELEASE_URL" && GOT=1
    fi
    if [ "$GOT" = 1 ] && tar -xzf "$TMP/m.tgz" -C "$OUT" 2>/dev/null && \
       [ -f "$OUT/model.onnx" ] && [ -f "$OUT/labels.json" ] && [ -f "$OUT/centroids.json" ]; then
        echo "The model is in $OUT (downloaded)"
    else
        echo 'No release model (or it did not unpack): teaching one here.'
        TRAIN_ONLY=1
    fi
    rm -rf "$TMP"
fi

if [ "$TRAIN_ONLY" = 1 ]; then
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
fi

# the runtime bit for the app's own Python (the wordpiece is pure Python in
# yumi/intents.py, so only onnxruntime is wanted; it is light)
echo
if python3 -c 'import onnxruntime' >/dev/null 2>&1; then
    echo 'onnxruntime: already here'
else
    echo 'onnxruntime: installing into the user site (pip install --user)...'
    python3 -m pip install --user --quiet onnxruntime || \
        echo 'Could not install onnxruntime: run «python3 -m pip install --user onnxruntime» by hand.' >&2
fi

echo
echo "Done: the model lives in $OUT."
echo 'Yumi picks it up on her next start; in her menu: Settings → Mind → Intents.'

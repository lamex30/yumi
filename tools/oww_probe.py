#!/usr/bin/env python
"""A spare ear for her name: openWakeWord, a model you train for one word alone.

The state of this work and the training process for another agent to pick up:
docs/handoff-oww.md.

  ./tools/oww_probe.py                 listen to the mic with the pretrained ears
  ./tools/oww_probe.py --models yumi   listen for a trained «Юми» model (see below)
  ./tools/oww_probe.py --models ~/.local/share/yumi/oww/models/yumi.onnx --debug
  ./tools/oww_probe.py --wav said.wav  read a wav file instead of the microphone
  ./tools/oww_probe.py --mics          list the microphones; --mic NAME picks one

openWakeWord is not vosk: it does not hear words at all, it hears one learned
name (each model is ~2 MB and costs ~7% of realtime on a modest CPU). The
pretrained ears are English names (hey jarvis, alexa...); for «Юми» a small
model has to be taught:

  1. Record or gather ~20 real sayings of the name (your own tape in /tmp/yumi
     will do), plus silero TTS renderings of «Юми», «Юм», «Юми, привет»...
  2. Cut them to 1.5 s clips, 16 kHz mono wav, and mix a copy with noise and
     reverb (the openWakeWord training pipeline does this for you:
     https://github.com/dscripka/openWakeWord/blob/main/notebooks and the
     automatic_model_training.ipynb in it).
  3. Train a small model (~5 min on a GPU, an evening on a CPU), convert to
     onnx, and point --models at the file. Anything under ~/.local/share/yumi/
     oww/models is a fair home for it.

The venv with openwakeword lives in ~/.local/share/yumi/oww (see OWW_HOME);
this probe borrows that Python when the running one has no openwakeword.
This is an experiment only: the ears (yumi/stt_daemon.py) stay vosk + whisper
until the probe proves itself on your desk.

The ear records through the same microphone the ears do: their echo-cancel
source «yumi_ec» when the daemon is up, the default source otherwise (--mic
names another one). When the captured sound is clipped (a hot mic gain crushes
the waveform at the ceiling), the probe says so — the model goes near-deaf on
clipped audio even though it hears a quiet clean whisper just fine, so a
clipping warning is worth more than any threshold tuning.
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

RATE = 16000
OWW_HOME = Path(os.environ.get('YUMI_OWW_HOME') or Path.home() / '.local/share/yumi/oww')
PRETRAINED = 'hey_jarvis_v0.1'      # a name to hear while no «Юми» model exists yet
THRESHOLD = 0.5                     # a score over this is the name heard
ECHO_SOURCE = 'yumi_ec'             # the ears' echo-cancel source (stt_daemon.py)
CLIP_LEVEL = 32700                  # a sample at or over this is a crushed wave


def oww_python():
    """a Python that has openwakeword: ours if it does, the venv's one if not"""
    try:
        import openwakeword      # noqa: F401
        return None
    except ImportError:
        oww = OWW_HOME / 'bin/python'
        if not oww.exists():
            sys.exit("openwakeword is nowhere: make the venv first —\n"
                     f"  uv venv {OWW_HOME} && uv pip install --python {oww} openwakeword")
        return str(oww)


def pw_mics():
    """Audio/Source node names, the way pw-record --target wants them; [] without pw-cli"""
    try:
        out = subprocess.run(['pw-cli', 'ls', 'Node'], capture_output=True,
                             text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    mics, cur = [], {}
    def flush():
        name = cur.get('node.name', '')
        if cur.get('media.class') == 'Audio/Source' and name and not name.endswith('.monitor'):
            mics.append(name)
    for line in out.splitlines():
        s = line.strip()
        if s.startswith('id '):
            flush()
            cur = {}
        elif '=' in s:
            k, _, v = s.partition('=')
            cur[k.strip()] = v.strip().strip('"')
    flush()
    return mics


def recorder(mic=None):
    """the same order of ears' recorders the daemon uses (raw s16 mono RATE);
    the ear aims at a real microphone: the echo-cancel source when the daemon
    is up (that is how you know), else the hardware mic (alsa_input.*) — the
    bare default source can be a filter's output, not an ear at all — or the
    one --mic names"""
    want = mic
    if mic is None:
        sources = pw_mics()
        if ECHO_SOURCE in sources:
            want = ECHO_SOURCE      # the daemon is running: hear what it hears
        else:
            real = [s for s in sources if s.startswith('alsa_input')]
            want = (real or sources or [None])[0]
    for name, cmd, mic_args in (
        ('pw-record', ['pw-record', '--raw', '--format', 's16', '--rate', str(RATE),
                       '--channels', '1', '-'], ['--target', '{mic}']),
        ('arecord', ['arecord', '-q', '-t', 'raw', '-f', 'S16_LE', '-r', str(RATE),
                     '-c', '1', '-'], ['-D', '{mic}']),
        ('parecord', ['parecord', '--raw', '--format=s16le', f'--rate={RATE}',
                      '--channels=1', '-'], ['--device={mic}']),
    ):
        if subprocess.run(['sh', '-c', f'command -v {name}'], capture_output=True).returncode == 0:
            if want:
                cmd = cmd[:-1] + [a.format(mic=want) for a in mic_args] + cmd[-1:]
            return cmd, want
    sys.exit('no parecord, pw-record or arecord for the microphone')


def clip_hint(pcm, np):
    """a word about a hot mic gain, when the captured wave is crushed at the ceiling"""
    hot = int((np.abs(pcm[::4]) >= CLIP_LEVEL).sum()) if len(pcm) else 0
    return hot > len(pcm[::4]) * 0.02


def listen(models, mic=None, debug=False, threshold=THRESHOLD):
    """read the mic and say the moment a model hears its name"""
    from openwakeword.model import Model
    import numpy as np
    m = Model(wakeword_models=models, inference_framework='onnx')
    cmd, aimed = recorder(mic)
    print('listening with %s (mic: %s, threshold %s) — say the name (Ctrl-C to stop)'
          % (', '.join(m.models.keys()), aimed or 'default', threshold), flush=True)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    warned, sec_max, sec_hot, sec_rms, deadline = False, 0.0, 0, 0, time.time() + 1.0
    try:
        while True:
            chunk = proc.stdout.read(1280)         # 80 ms of sound
            if not chunk:
                break
            pcm = np.frombuffer(chunk, dtype=np.int16)
            sec_hot += int((np.abs(pcm) >= CLIP_LEVEL).sum())
            sec_rms += int(np.sqrt(np.mean(pcm.astype(np.int64)**2)))
            for name, score in m.predict(pcm).items():
                sec_max = max(sec_max, score)
                if score > threshold:
                    print(f'heard: {name} ({score:.2f})', flush=True)
                    m.reset()
            if time.time() >= deadline:            # one report a second
                if sec_hot > RATE * 0.02 and not warned:
                    warned = True
                    print('the mic is clipping: the wave is crushed at the ceiling, and the '
                          'model is near-deaf on that. Turn the gain down —\n'
                          "  amixer -c 0 sset 'Internal Mic Boost' 1     # 0..3, this desk wants 1\n"
                          "  amixer -c 0 sset 'Capture' 38               # 0..63\n"
                          "  amixer -c 0 sset 'Digital' 85               # 0..120",
                          flush=True)
                if debug:
                    print('mic level %5d | max score last second: %.2f%s'
                          % (sec_rms // 13, sec_max,
                             '  (clipping!)' if sec_hot > RATE * 0.02 else ''), flush=True)
                sec_max, sec_hot, sec_rms = 0.0, 0, 0
                deadline = time.time() + 1.0
    except KeyboardInterrupt:
        print('stop', flush=True)


def read_wav(models, path, threshold=THRESHOLD):
    """feed a wav file to the ear: every moment above the threshold is printed,
    and the last line is always `max score (X.XX)` — the best of the whole tape,
    even when it stayed under the threshold (compare_wake reads that line).

    A second of silence goes on both ends, the way the model is scored in
    training/eval (oww-work/eval_model.py, stock predict_clip): a clip of
    0.3–1.2 s is shorter than the model's own 16-frame window, and without
    the quiet lead-in it never builds up context — a live mic always has that
    context, the bare file does not."""
    import wave
    from openwakeword.model import Model
    import numpy as np
    with wave.open(str(path), 'rb') as w:
        assert w.getframerate() == RATE and w.getnchannels() == 1, \
            f'{path}: a 16 kHz mono wav is wanted'
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    pcm = np.concatenate([np.zeros(RATE, dtype=np.int16), pcm,
                          np.zeros(RATE, dtype=np.int16)])
    m = Model(wakeword_models=models, inference_framework='onnx')
    n = len(pcm)
    hits, best = 0, 0.0
    # every window is scored, padding included — the model decides on the last
    # 16 frames of history, so its peak lands just AFTER the word ends, inside
    # the tail padding (eval_model.score_wav scores the padded tape the same way)
    for i in range(0, n - 1280, 1280):
        for name, score in m.predict(pcm[i:i + 1280]).items():
            best = max(best, score)
            if score > threshold:
                hits += 1
                print(f'{max(0.0, (i - RATE)/RATE):6.2f} s  {name} ({score:.2f})', flush=True)
    if clip_hint(pcm, np):
        print('this tape is clipped (the mic gain was too hot when it was made) — '
              'a live ear hears a clean voice far better', flush=True)
    if not hits:
        print('nothing heard', flush=True)
    print(f'max score ({best:.2f})', flush=True)


def main():
    ap = argparse.ArgumentParser(description="A spare ear for her name: openWakeWord")
    ap.add_argument('--models', nargs='*', default=[PRETRAINED],
                    help='onnx model names or paths (default: the pretrained %s)' % PRETRAINED)
    ap.add_argument('--wav', help='read this wav file instead of the microphone')
    ap.add_argument('--mic', help='the microphone for pw-record --target / arecord -D')
    ap.add_argument('--mics', action='store_true', help='list the microphones and exit')
    ap.add_argument('--debug', action='store_true',
                    help='print the max score every second, so the ear is seen thinking')
    ap.add_argument('--threshold', type=float, default=THRESHOLD,
                    help='a score over this is the name heard (default: %s). Lower it for '
                         'recall — the ears\' vosk+whisper judge absorbs the false wakes' % THRESHOLD)
    args = ap.parse_args()
    if args.mics:
        for m in pw_mics():
            print(m, flush=True)
        return
    models = []
    for name in args.models:
        p = Path(name).expanduser()
        models.append(str(p) if p.exists() else name)
    if args.wav:
        read_wav(models, args.wav, threshold=args.threshold)
    else:
        listen(models, mic=args.mic, debug=args.debug, threshold=args.threshold)


if __name__ == '__main__':
    oww = oww_python()          # the probe borrows the venv's python when ours has no openwakeword
    if oww and os.path.realpath(sys.executable) != os.path.realpath(oww):
        os.execv(oww, [oww, '-u', __file__] + sys.argv[1:])   # -u: the ear speaks at once, even into a pipe
    main()

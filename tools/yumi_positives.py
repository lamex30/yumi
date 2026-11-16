#!/usr/bin/env python
"""Gather the name from her human: the positives the trained ear starves without.

The wake model was taught on silero's voices alone and goes deaf on a live
throat, so its recall on the desk is near zero. Two ways to feed it the real
thing, both writing 16 kHz mono s16 clips into
~/.local/share/yumi/oww-work/user_positives/:

  record   the human speaks: N utterances of «Юми», each cut by an energy gate
           and padded with quiet before and after (the model wants ~0.6 s of
           sound after the name to finish its features)
             python3 tools/yumi_positives.py record --n 40
             python3 tools/yumi_positives.py record --review   # listen, keep or throw

  harvest  the ears' own tapes are mined: vosk marks every «юми»-like word with
           its time, the segment around it is cut out. False hits happen —
           review them the same way (--review).
             python3 tools/yumi_positives.py harvest            # /tmp/yumi tapes
             python3 tools/yumi_positives.py harvest --wavs a.wav b.wav

           vosk lives in the stt venv; when the running python has none, the
           script borrows that python (the probe does the same with oww).

The clips feed openWakeWord training (my_model.yaml) — review before training:
a false positive in the shelf teaches the ear a wrong name.
"""
import argparse
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import time
import wave
from array import array
from pathlib import Path

RATE = 16000
OUT = Path(os.environ.get('YUMI_POSITIVES') or
           Path.home() / '.local/share/yumi/oww-work/user_positives')
SETTINGS = Path.home() / '.config/yumi/settings.json'
NAME_ALIKE = ('юми', 'уми', 'уме', 'юмиа', 'юмия', 'юмию')   # what vosk calls the name
PAD_BEFORE = 0.4     # s of quiet before the name
PAD_AFTER = 0.9      # s of quiet after: the model wants the tail context
MIN_SPEECH = 0.15    # shorter than this and the clip is a phantom


def read_settings():
    try:
        return json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        return {}


def mic_source():
    """the mic the ears listen with: the daemon's choice, the default otherwise"""
    st = read_settings()
    mic = st.get('stt_mic') or ''
    if not mic or not _source_exists(mic):
        mic = _default_source() or mic
    return mic


def _source_exists(name):
    try:
        out = subprocess.run(['pactl', 'list', 'short', 'sources'],
                             capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return True                     # no pactl: trust the name
    return name in out


def _default_source():
    try:
        out = subprocess.run(['pactl', 'get-default-source'],
                             capture_output=True, text=True, timeout=3).stdout.strip()
        return out or None
    except (OSError, subprocess.SubprocessError):
        return None


def recorder_cmd(mic):
    """the same argv the ears' daemon uses, so the tape sounds as the wake hears it"""
    if shutil.which('pw-record'):
        return ['pw-record', '--raw', '--format', 's16', '--rate', str(RATE),
                '--channels', '1', '--target', mic, '-']
    if shutil.which('parecord'):
        return ['parecord', '--raw', '--format=s16le', f'--rate={RATE}',
                '--channels=1', '--device=' + mic, '-']
    return None


def record_seconds(sec, mic):
    """s16 mono bytes of `sec` seconds from the mic; None when the mic is gone"""
    cmd = recorder_cmd(mic)
    if not cmd:
        print('  no pw-record or parecord: cannot record')
        return None
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    want = int(sec * RATE * 2)
    buf = bytearray()
    try:
        while len(buf) < want:
            chunk = p.stdout.read(4000)
            if not chunk:
                break
            buf += chunk
    except KeyboardInterrupt:
        pass
    finally:
        p.terminate()
        try:
            p.wait(timeout=2)
        except subprocess.TimeoutExpired:
            p.kill()
        p.stdout.close()
    return bytes(buf[:want]) if len(buf) >= RATE * 2 else None


def speech_span(pcm):
    """(start, end) samples of speech over an energy gate: the floor is the
    quietest third of the frames, speech is far above it"""
    frames = [pcm[i:i + 400] for i in range(0, len(pcm) - 400, 400)]
    if not frames:
        return None
    rms = [max(1, (sum(x * x for x in f) // max(1, len(f))) ** 0.5) for f in frames]
    floor = sorted(rms)[len(rms) // 3]
    hi = max(floor * 3.0, 200.0)
    on = [v > hi for v in rms]
    first = next((i for i, v in enumerate(on) if v), None)
    if first is None:
        return None
    last = max(i for i, v in enumerate(on) if v)
    if (last - first + 1) * 400 / RATE < MIN_SPEECH:
        return None
    return first * 400, (last + 1) * 400


def save_wav(path, pcm):
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)


def normalize(pcm, target_db=-20.0):
    """speech to a healthy level: the laptop mic records −40 dBFS, the training
    tape should sound like a voice a meter away; the floor is −60..−90 so there
    is headroom to gain up without lifting hiss into the word"""
    frames = [pcm[i:i + 400] for i in range(0, len(pcm) - 400, 400)]
    if not frames:
        return pcm
    rms = [(sum(x * x for x in f) / max(1, len(f))) ** 0.5 for f in frames]
    speech = sorted(rms)[len(rms) // 2]       # the word is half the clip or more
    if speech < 50:
        return pcm
    want = 10 ** (target_db / 20) * 32768     # the rms we are after, as a level
    gain = min(want / speech, 100.0)
    peak = max(abs(x) for x in pcm)
    if peak * gain > 32000:                   # the loudest moment caps the gain
        gain = 32000 / peak
    out = array('h', (max(-32768, min(32767, int(x * gain))) for x in pcm))
    return out.tobytes()


def cut_around(pcm, a, b):
    """the clip with quiet on both sides: quiet is borrowed from the tape itself
    when there is any, zeros fill the rest"""
    pre, post = int(PAD_BEFORE * RATE), int(PAD_AFTER * RATE)
    start = max(0, a - pre)
    end = min(len(pcm), b + post)
    lead = a - start                      # real quiet found before the name
    head = b'\x00\x00' * (pre - lead)
    tail = b'\x00\x00' * max(0, post - (len(pcm) - b))
    return pcm[start:end], head, tail


def next_index():
    OUT.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in OUT.glob('*.wav'):
        try:
            n = max(n, int(f.stem))
        except ValueError:
            pass
    return n + 1


def play(path):
    for player in ('paplay', 'pw-play'):
        if shutil.which(player):
            subprocess.run([player, str(path)], stderr=subprocess.DEVNULL)
            return


def review(paths):
    """listen to each clip and keep or throw it: the shelf must hold only true names"""
    if not paths or not sys.stdin.isatty():
        return
    print(f'\n— review {len(paths)} clips: play, then keep (Enter) or throw (n)')
    for p in paths:
        play(p)
        try:
            ans = input(f'  {p.name}: keep? [Y/n] ').strip().lower()
        except (EOFError, KeyboardInterrupt):
            break
        if ans in ('n', 'no', 'н'):
            p.unlink()
            print('  thrown')


def cmd_record(n, review_after, secs):
    mic = mic_source()
    print(f'the name from the human: {n} utterances of «Юми»\n'
          f'  mic: {mic}\n'
          f'  clips: {OUT}\n'
          '  say the name alone, at your usual volume, varying tone and distance\n')
    made = []
    for i in range(n):
        idx = next_index()
        for s in (3, 2, 1):
            print(f'  [{i + 1}/{n}] {s}...', flush=True)
            time.sleep(0.8)
        pcm_bytes = record_seconds(secs, mic)
        if pcm_bytes is None:
            print('  the mic gave nothing: try again')
            continue
        pcm = struct.unpack(f'<{len(pcm_bytes) // 2}h', pcm_bytes)
        span = speech_span(pcm)
        if span is None:
            print('  nothing loud enough was heard: try again')
            continue
        a, b = span
        clip, head, tail = cut_around(pcm_bytes, a * 2, b * 2)   # byte offsets
        data = normalize(head + clip + tail)
        path = OUT / f'{idx:04d}.wav'
        save_wav(path, data)
        made.append(path)
        print(f'  saved {path.name} ({(b - a) / RATE:.2f} s of speech)')
    print(f'\n{len(made)} clips in {OUT}')
    if review_after:
        review(made)


def load_vosk():
    try:
        from vosk import Model, KaldiRecognizer       # noqa: F401
        return sys.executable
    except ImportError:
        pass
    for py in [os.environ.get('YUMI_STT_PY'),
               str(Path.home() / '.local/share/yumi/stt/bin/python')]:
        if py and os.path.isfile(py):
            return py
    return None


VOSK_REEXEC = r'''
import json, sys, wave
from vosk import Model, KaldiRecognizer
model = Model(sys.argv[2])
for f in sys.argv[3:]:
    try:
        wf = wave.open(f)
        if wf.getframerate() != 16000:
            print(json.dumps({"file": f, "error": "rate"})); continue
        r = KaldiRecognizer(model, 16000); r.SetWords(True)
        words = []
        while True:
            d = wf.readframes(8000)
            if not d: break
            if r.AcceptWaveform(d):
                words += json.loads(r.Result()).get("result", [])
        words += json.loads(r.FinalResult()).get("result", [])
        print(json.dumps({"file": f, "words": words}))
    except Exception as e:
        print(json.dumps({"file": f, "error": str(e)}))
'''


def cmd_harvest(wavs, review_after):
    py = load_vosk()
    if not py:
        print('vosk is nowhere: the stt venv is gone')
        return
    model_home = (os.environ.get('YUMI_STT_MODEL') or
                  str(Path.home() / '.local/share/yumi/stt/vosk-model-small-ru-0.22'))
    if not os.path.isdir(model_home):
        print(f'the vosk model is not here: {model_home}')
        return
    OUT.mkdir(parents=True, exist_ok=True)
    report = []
    idx = next_index()
    for f in wavs:
        out = subprocess.run([py, '-c', VOSK_REEXEC, 'harvest', model_home, f],
                             capture_output=True, text=True, timeout=300)
        for line in out.stdout.splitlines():
            try:
                j = json.loads(line)
            except ValueError:
                continue
            if 'error' in j:
                print(f'  {Path(j["file"]).name}: {j["error"]}')
                continue
            wf = wave.open(j['file'])
            pcm = wf.readframes(wf.getnframes())
            for wd in j['words']:
                word = wd['word'].lower()
                if not any(k in word for k in NAME_ALIKE):
                    continue
                a = max(0, int((wd['start'] - PAD_BEFORE) * RATE))
                b = min(len(pcm), int((wd['end'] + PAD_AFTER) * RATE))
                clip = pcm[a * 2:b * 2]
                if len(clip) < RATE * 2 * 0.5:
                    continue
                path = OUT / f'h{idx:04d}_{Path(j["file"]).stem}_{wd["start"]:.0f}.wav'
                save_wav(path, clip)
                report.append((path.name, j['file'], wd['start'], word))
                idx += 1
    if report:
        print(f'\n{len(report)} candidates in {OUT}:')
        for name, src, t, word in report:
            print(f'  {name}  {Path(src).name} @ {t:7.2f} s  vosk: {word}')
    else:
        print('nothing name-like in these tapes')
    if review_after:
        review([OUT / r[0] for r in report])


def cmd_normalize(wavs):
    """healthy level for clips already on the shelf: the mic is quiet, the tape
    must not be"""
    done = 0
    for f in wavs:
        with wave.open(str(f), 'rb') as w:
            if w.getframerate() != RATE or w.getnchannels() != 1 or w.getsampwidth() != 2:
                print(f'  {Path(f).name}: not 16 kHz mono s16, skipped')
                continue
            data = w.readframes(w.getnframes())
        data = normalize(data)
        save_wav(f, data)
        done += 1
    print(f'{done} clips normalized in place')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('record', help='speak the name, the tape is cut automatically')
    r.add_argument('--n', type=int, default=40)
    r.add_argument('--secs', type=float, default=3.0,
                   help='seconds of tape per utterance (default: 3)')
    r.add_argument('--review', action='store_true', help='listen and keep or throw')
    h = sub.add_parser('harvest', help='mine vosk-marked «юми» from the ears\' tapes')
    h.add_argument('--wavs', nargs='*', default=[],
                   help='tapes to read (default: every ears-*.wav in /tmp/yumi)')
    h.add_argument('--review', action='store_true')
    nrm = sub.add_parser('normalize', help='bring already saved clips to a healthy level')
    nrm.add_argument('--wavs', nargs='*', default=[])
    args = ap.parse_args()
    if args.cmd == 'record':
        cmd_record(args.n, args.review, args.secs)
    elif args.cmd == 'normalize':
        wavs = args.wavs or sorted(map(str, OUT.glob('*.wav')))
        cmd_normalize(wavs)
    else:
        wavs = args.wavs or sorted(map(str, Path('/tmp/yumi').glob('ears-*.wav')))
        if not wavs:
            print('no tapes to read: the ears keep them in /tmp/yumi while they live')
            return
        cmd_harvest(wavs, args.review)


if __name__ == '__main__':
    main()

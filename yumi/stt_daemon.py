#!/usr/bin/env python3
"""Her ears: local Vosk and faster-whisper over the microphone as a tiny HTTP server.

Yumi's app side (yumi/ears.py) starts this with a Python that has vosk (see
tools/stt_setup.sh) and asks it to listen: /listen?secs=8&lang=ru. The small models
(~45 MB each, Russian and English) are downloaded on the first use into
~/.local/share/yumi/stt; after that a phrase takes a moment.

The phrase itself is recognized by faster-whisper (a small whisper, int8 on the
CPU, also fully local): vosk stays only as the ear that notices the phrase has
ended, and whisper reads the tape and writes the words — it hears far better,
even the small model. The first listen downloads the whisper model (~460 MB)
into ~/.local/share/yumi/stt/whisper; when whisper is not there or fails, the
vosk's own words are used instead.

The microphone is read with whatever the desktop has: parecord, pw-record or arecord.
A particular microphone can be named (mic=... from the settings; /mics lists the ones
the current recorder can hear).
A click on her mic button starts a listen; vosk hears the end of the phrase, or
secs pass, or another click asks /stop. /wake is the same microphone held on longer:
vosk waits for the wake word (юми), and the moment it passes by the app gets the
phrase that follows — nothing is recognized twice. His word is a guess, so before
she wakes, the tape of the last seconds goes to whisper for a yes or a no.

Before the sound goes anywhere it is cleaned here: a one-pole high-pass (hum and
rumble go), a noise gate with a floor that follows the quiet (steady hiss and fan
die), and a webrtc voice detector (webrtcvad, best effort) that finds where speech
really ends, so the models do not guess the end from their own timing.

On top of the microphone there is a webrtc echo-cancel source (module-echo-cancel
over pactl, best effort): the chosen mic feeds it, the ears record from the clean
end, and her own sounds played into the echo-cancel sink are subtracted — she never
hears herself. When it cannot be set up, the ears fall back to the plain mic.

Two sizes per language: the small one is quick and always on, the big one hears
better still; the app asks for it with size=big in the settings (Hearing accuracy).
"""
import argparse
import atexit
import hashlib
import json
import os
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import wave
import zipfile
from array import array
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOME = Path(os.environ.get('YUMI_STT_HOME') or Path.home() / '.local/share/yumi/stt')
STATE = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'yumi' / 'stt' / 'port.json'
RATE = 16000                            # the small models want 16 kHz mono s16
MODELS = {                              # lang: size: (zip on alphacephei.com, folder inside it)
    'ru': {'small': ('vosk-model-small-ru-0.22', 'vosk-model-small-ru-0.22'),
           'big': ('vosk-model-ru-0.42', 'vosk-model-ru-0.42')},
    'en': {'small': ('vosk-model-small-en-us-0.15', 'vosk-model-small-en-us-0.15'),
           'big': ('vosk-model-en-us-0.22', 'vosk-model-en-us-0.22')},
}
WHISPER_SIZE = {'small': 'small',       # faster-whisper's own names for the sizes
                'big': 'large-v3'}
VERSION = 27                            # bumped when the protocol changes: stale twins are restarted
                                        # (22: the ear's veto softened into a rank: a deaf ear alone
                                        #     no longer silences a name-shaped word — the arbitrator
                                        #     weighs every ear, the judge reads a short fresh tape,
                                        #     and the wake keeps a jsonl diary of its doubts)
                                        # (21: the trained ear vetoes vosk's dreams when it hears nothing)
                                        # (20: whoever reads the tape judges — nemotron or whisper)
                                        # (19: the mute may be lifted (secs=0), a «стоп» shout
                                        #     gets through it, and the wide wake match keeps
                                        #     the word that follows the name)
WAKE_AFTER = 1.5                        # s of quiet after the name before she asks what you wanted
WAKE_TAPE = RATE * 8 * 2                # bytes of the recent sound the judge reads: ~8 s
WAKE_REJUDGE = 3.0                      # s before the judge reads the tape again after a dream
VAD_TAIL = 1200                         # ms of quiet after speech: the phrase is really over
WAKE_SENSITIVITY = {'normal': 1, 'strict': 0}  # edit-distance budget for a wake match
WAKE_WIDE = 3                 # when the judge is on duty, the pre-filter forgives three

OWW_THRESHOLD = 0.5          # the trained ear's score that counts as her name
OWW_SURE = 0.7               # above it the name is sure: the judge is not asked
OWW_VETO = 0.2               # below it the ear hears nothing at all: with the arbitrator
                             # this is no longer a veto but a rank — a name-shaped word
                             # vosk wrote still reaches the judge (the rate limit keeps
                             # a video's speech from being read over and over)
OWW_HOLD = 0.35              # hysteresis: a believed candidate stays believed while the
                             # score keeps above this line — it takes a real drop, not a
                             # dip between the syllables, to close the door again
OWW_SPIKE = 2                # frames in a row above the threshold before a candidate is
                             # believed: one spike is a knock, a stretch is a name
OWW_MS = 80.0                # ms of sound one frame of the trained ear stands for
JUDGE_TAPE = 5.0             # s of tape the judge reads: some sound before the name,
                             # the name, what follows — not the whole 8 s shelf
JUDGE_TTL = 10.0             # s a judge's verdict stays fresh: an older answer speaks
                             # of a tape that is no longer on the table
ARB_IGNORE, ARB_FAST, ARB_JUDGE = 'IGNORE', 'FAST_WAKE', 'SEND_JUDGE'
OWW_CHUNK = 2560             # bytes of s16 sound per frame: 1280 samples, 80 ms —
                             # the exact step the streaming melspectrogram keeps step with
                              # letters: over music vosk writes «юноша» for «юми», and
                              # only whisper reading the tape tells a name from a dream
OWW_COOLDOWN = 2.0           # s of quiet after a wake before the ear may wake again:
                             # the same name still ringing on the air must not fire twice
WAKE_LEAD_MS = 1000.0        # ms of sound before the wake point that joins the phrase:
                             # OpenWakeWord answers a frame or two INTO the name, the
                             # head must be on the tape when STT reads it
WAKE_LISTEN = 15.0           # s the wake keeps listening for the phrase after the name
TAP_DIR = Path(os.environ.get('YUMI_TAP_DIR') or '/tmp/yumi')   # the ears' diary lives here
TAP_SEGMENT = 600.0                     # s of tape in one wav, then the next file starts
TAP_MAX_BYTES = 10 * 1024 * 1024        # a segment is no bigger than this: the diary is small
TAP_KEEP = 5                            # only the newest segments stay: the diary is a ring
TAP_LINES = 1000                        # recogn.txt holds the freshest this many lines

ECHO_SOURCE = 'yumi_ec'                 # the echo-cancel source the ears record through
ECHO_SINK = 'yumi_ec_sink'              # where her own sounds go, so they can be subtracted

NEMO_DIR = HOME / 'sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11'
                                        # the optional ear the settings may name
                                        # (stt_engine: 'nemotron'): a streaming 0.6b
                                        # transducer, ~650 MB, ru and en among 40 locales,
                                        # tools/nemotron_setup.sh puts it here
_echo = {'on': False, 'master': None, 'module': None, 'checked': 0.0, 'failed': 0}
_echo_gate = threading.Lock()   # echo_ready runs from several threads: one at a time

_mute_lock = threading.Lock()
_mute_until = 0.0                       # her ears are closed until this moment


def muted():
    """True while the ears are told to be deaf: her own voice is playing, so every
    chunk the recorder still gives is dropped before vosk, the detector or the
    judge's tape see it — she cannot wake to her own words"""
    with _mute_lock:
        return time.monotonic() < _mute_until


def mute_for(secs):
    """close the ears for this many seconds; a fresh call stretches the window,
    secs=0 opens them again (her voice stopped early: a «стоп», a click, a new line)"""
    global _mute_until
    with _mute_lock:
        _mute_until = max(_mute_until, time.monotonic() + secs) if secs > 0 else 0.0
# which command reads the mic, raw s16 mono RATE. pw-record before parecord: on some
# desktops parecord connects and then stays silent, so arecord gets the last word.
# {mic} in the third column is where a chosen microphone goes (the /mics endpoint lists them).
RECORDER = [
    ('pw-record', ['pw-record', '--raw', '--format', 's16', '--rate', str(RATE),
                   '--channels', '1', '-'], ['--target', '{mic}']),
    ('arecord', ['arecord', '-q', '-t', 'raw', '-f', 'S16_LE', '-r', str(RATE), '-c', '1', '-'],
     ['-D', '{mic}']),
    ('parecord', ['parecord', '--raw', '--format=s16le', f'--rate={RATE}',
                  '--channels=1', '--latency-msec=100', '-'], ['--device={mic}']),
]


def recorder_cmd(mic=None):
    """(name, argv) that reads the mic; a chosen microphone is named before the '-'"""
    for name, cmd, mic_args in RECORDER:
        if shutil.which(name):
            if mic:                     # the last '-' is the pipe: options go right before it
                cmd = cmd[:-1] + [a.format(mic=mic) for a in mic_args] + cmd[-1:]
            return name, cmd
    return None, None


def _pactl_short(what):
    try:
        return subprocess.run(['pactl', 'list', 'short', what], capture_output=True,
                              text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return ''


def _echo_want(mic):
    """the mic the canceller should listen to: the chosen one, but never its own clean
    end (that would be a loop) and never a monitor of an output"""
    if mic and mic != ECHO_SOURCE and not mic.endswith('.monitor'):
        return mic
    return None


def _echo_enabled():
    """the canceller is best effort, so the user can turn it off: 'stt_echo': false in
    ~/.config/yumi/settings.json (the ears fall back to the plain chosen mic)"""
    try:
        st = json.loads((Path.home() / '.config' / 'yumi' / 'settings.json').read_text())
        return bool(st.get('stt_echo', True))
    except (OSError, ValueError):
        return True


def _ear_tuning():
    """the phrase's timing can be the user's own: 'phrase_tail_ms' (the quiet after
    speech that ends a phrase) and 'wake_after' (the quiet after the name) in
    ~/.config/yumi/settings.json"""
    try:
        st = json.loads((Path.home() / '.config' / 'yumi' / 'settings.json').read_text())
        return (max(300, min(3000, int(st.get('phrase_tail_ms') or VAD_TAIL))),
                max(0.5, min(5.0, float(st.get('wake_after') or WAKE_AFTER))))
    except (OSError, ValueError):
        return VAD_TAIL, WAKE_AFTER


VAD_TAIL, WAKE_AFTER = _ear_tuning()    # the user's own timing, when settings.json says so


def _wake_tuning():
    """the wake's own dials, when settings.json says so: the trained ear's
    thresholds ('oww_threshold', 'oww_sure', 'oww_hold', 'oww_veto'), its memory
    ('oww_window' s, 'oww_spike' frames, 'oww_positive_ms'), the judge's share
    ('judge_tape' s of sound, 'judge_rate' s between reads, 'judge_ttl' s a
    verdict stays fresh), the phonetic net ('wake_phonetic'), the raw stream
    ('oww_stream_raw': the ear and the judge read the sound before the noise
    gate, so quiet letters stay whole) and the debug wavs ('stt_diag_wav').
    Every dial keeps a safe default: an old settings.json wakes just as before."""
    t = {'oww_threshold': OWW_THRESHOLD, 'oww_sure': OWW_SURE, 'oww_hold': OWW_HOLD,
         'oww_veto': OWW_VETO, 'oww_window': 3.0, 'oww_spike': OWW_SPIKE,
         'oww_positive_ms': 240.0, 'judge_tape': JUDGE_TAPE, 'judge_rate': WAKE_REJUDGE,
         'judge_ttl': JUDGE_TTL, 'wake_phonetic': True, 'oww_stream_raw': True,
         'stt_diag_wav': False, 'oww_cooldown': OWW_COOLDOWN,
         'wake_lead_ms': WAKE_LEAD_MS, 'wake_listen': WAKE_LISTEN}

    def clamp(key, lo, hi):
        try:
            t[key] = min(hi, max(lo, float(st[key])))
        except (TypeError, ValueError, KeyError):
            pass

    try:
        st = json.loads((Path.home() / '.config' / 'yumi' / 'settings.json').read_text())
    except (OSError, ValueError):
        return t
    for key in ('oww_threshold', 'oww_sure', 'oww_hold', 'oww_veto'):
        clamp(key, 0.0, 1.0)
    clamp('oww_window', 1.0, 10.0)
    clamp('oww_spike', 1.0, 6.0)
    clamp('oww_positive_ms', 0.0, 3000.0)
    clamp('judge_tape', 2.0, 8.0)
    clamp('judge_rate', 1.0, 30.0)
    clamp('judge_ttl', 2.0, 60.0)
    clamp('oww_cooldown', 0.0, 30.0)
    clamp('wake_lead_ms', 0.0, 4000.0)
    clamp('wake_listen', 5.0, 30.0)
    t['oww_spike'] = int(t['oww_spike'])
    for key in ('wake_phonetic', 'oww_stream_raw', 'stt_diag_wav'):
        try:
            t[key] = bool(st[key])
        except (TypeError, ValueError, KeyError):
            pass
    return t


TUNE = _wake_tuning()                   # the wake's dials, read once at the start


def _default_sink():
    """the name of the output the desktop plays into right now, or None"""
    try:
        r = subprocess.run(['pactl', 'get-default-sink'], capture_output=True,
                           text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() or None


def _echo_sink_master():
    """the real output the canceller's sink feeds: the default one, but never the
    canceller's own sink (a reload after the claim would loop through itself)"""
    sinks = [line.split('\t')[1] for line in _pactl_short('sinks').splitlines()
             if '\t' in line]
    real = [s for s in sinks if s != ECHO_SINK and not s.startswith(ECHO_SINK + '.')]
    if not real:
        return False            # no real output: the canceller would loop through itself
    want = _default_sink()
    return want if want in real else real[0]


def _vol_pct(name):
    """the quietest channel's volume percent of a sink, or None; pactl's long text"""
    try:
        r = subprocess.run(['pactl', 'get-sink-volume', name], capture_output=True,
                           text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    pcts = [int(m) for m in re.findall(r'/\s*(\d+)\s*%', r.stdout)]
    return min(pcts) if pcts else None


def _uncap_real_sink():
    """the real output must never be quieter than the knob: after the claim the volume
    slider and keys turn the canceller's sink, so a real end left low (an old volume,
    a reload's restore) caps everything — cranked up full and still barely audible"""
    virtual = _vol_pct(ECHO_SINK)
    master = _echo_sink_master()
    real = _vol_pct(master) if master else None
    if virtual is None or real is None or real >= virtual:
        return
    subprocess.run(['pactl', 'set-sink-volume', master, f'{virtual}%'],
                   capture_output=True, timeout=3)
    log(f'the real output was quieter than the knob, raised it to {virtual}%')


def _claim_default_sink():
    """the sounds the computer itself plays go into the canceller's sink too, so her
    ears subtract them: a video's speech must not pass for a voice in the room"""
    if _default_sink() != ECHO_SINK:
        subprocess.run(['pactl', 'set-default-sink', ECHO_SINK],
                       capture_output=True, timeout=3)
        log("the computer's sounds now play through the canceller's sink")
        _uncap_real_sink()


def _echo_load(master):
    """pactl loads the webrtc echo-cancel around the mic; True when the clean end appeared"""
    sink_master = _echo_sink_master()
    if sink_master is False:
        log('echo-cancel refused: no real output for its sink')
        return False
    argv = ['pactl', 'load-module', 'module-echo-cancel',
            f'source_name={ECHO_SOURCE}', f'sink_name={ECHO_SINK}', 'aec_method=webrtc']
    if master:
        argv.append(f'source_master={master}')
    if sink_master:
        argv.append(f'sink_master={sink_master}')
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    if r.returncode == 0 and ECHO_SOURCE in _pactl_short('sources'):
        _claim_default_sink()
        try:
            _echo['module'] = int(r.stdout.strip())
        except ValueError:
            _echo['module'] = None
        _echo['master'] = master        # what the canceller listens to now
        log('echo-cancel is on' + (f' (master {master})' if master else ''))
        return True
    if r.stderr and r.stderr.strip():
        log('echo-cancel refused: ' + r.stderr.strip()[-120:])
    return False


def echo_ready(mic=None):
    """the name of the echo-cancel source to record through, or None when there is none:
    best effort, it loads over the chosen mic (her sounds into ECHO_SINK give it the echo
    to subtract). pactl is not free, so the state is re-checked at most every 20 s"""
    with _echo_gate:            # a race here loads the canceller twice: CPU twice over
        now = time.monotonic()
        if now - _echo['checked'] >= 20:
            _echo['checked'] = now
            if not _echo_enabled():     # off in the settings: plain mic only, take the
                ids = []                # canceller apart and give the desktop its sink back
                for line in _pactl_short('modules').splitlines():
                    p = line.split('\t')
                    if len(p) >= 3 and 'module-echo-cancel' in p[1] and ECHO_SOURCE in p[2]:
                        ids.append(p[0])
                for mid in ids:
                    subprocess.run(['pactl', 'unload-module', mid],
                                   capture_output=True, timeout=5)
                if _default_sink() == ECHO_SINK:
                    real = _echo_sink_master()
                    if real:
                        subprocess.run(['pactl', 'set-default-sink', real],
                                       capture_output=True, timeout=3)
                if ids or _echo['on']:
                    log('echo-cancel is off (stt_echo: false in the settings)')
                _echo['on'] = _echo['master'] = _echo['module'] = None
                return None
            if ECHO_SOURCE in _pactl_short('sources'):
                _echo['on'] = True
                _uncap_real_sink()      # the knob above the real end must not be capped
                ids = []                    # every canceller around the mic, oldest first
                for line in _pactl_short('modules').splitlines():
                    p = line.split('\t')
                    if len(p) >= 3 and 'module-echo-cancel' in p[1] and ECHO_SOURCE in p[2]:
                        try:
                            ids.append(int(p[0]))
                        except ValueError:
                            pass
                        for arg in p[2].split():     # what the running canceller listens to
                            if arg.startswith('source_master='):
                                _echo['master'] = arg.split('=', 1)[1] or None
                if ids:                     # one is enough: two webrtc ends burn the core
                    _echo['module'] = ids[-1]
                    for extra in ids[:-1]:  # a twin from an old race or a lost unload goes
                        subprocess.run(['pactl', 'unload-module', str(extra)],
                                       capture_output=True, timeout=5)
            elif _echo['failed'] < 3:       # it refused three times: give up until a restart
                _echo['on'] = _echo_load(_echo_want(mic))
                if not _echo['on']:
                    _echo['failed'] += 1
        if _echo['on'] and mic and _echo_want(mic) != _echo['master']:
            want = _echo_want(mic)
            _echo['master'] = want          # what it will listen to from now on
            if _echo['module'] is not None:
                subprocess.run(['pactl', 'unload-module', str(_echo['module'])],
                               capture_output=True, timeout=5)
            if not _echo_load(want):        # the reload failed: the plain mic is next best
                _echo['on'], _echo['master'], _echo['module'] = False, None, None
    return ECHO_SOURCE if _echo['on'] else None


HP_A = 0.9607                    # one-pole high-pass ~100 Hz: hum and rumble go, speech stays
GATE_MIN = 260                   # s16: quieter than this is the room itself, not a voice
DAMP_DEEP, DAMP_SOFT = 0.12, 0.35   # how much of the sound passes the closed and half-open gate
EAR_RANGE = {                    # how far the ears reach: the gate decides whose speech counts —
    'near': (800, 6.0),          # an arm's length: a murmur from the far corner dies
    'mid':  (GATE_MIN, 3.0),     # the usual ear, the one she always had
    'far':  (120, 2.2),          # across the room: even a quiet voice passes the gate
}


def ear_gate(ear):
    """(gate floor minimum, floor factor) the noise gate listens with"""
    return EAR_RANGE.get(ear, EAR_RANGE['mid'])


def hp_pass(chunk, st):
    """one chunk of raw s16 mono through the high-pass: hum and rumble go; st carries
    the filter's last output and last input between the chunks"""
    n = len(chunk) // 2
    if n == 0:
        return chunk
    hp = st.get('hp') or 0.0
    prev = st.get('prev') or 0.0
    vals = array('h', chunk[:n * 2])
    for i in range(n):
        x = vals[i]
        hp = HP_A * (hp + x - prev)
        prev = x
        v = int(hp)
        if v > 32767:
            v = 32767
        elif v < -32768:
            v = -32768
        vals[i] = v
    st['hp'] = hp
    st['prev'] = prev
    return vals.tobytes()


def gate_noise(chunk, st, voiced):
    """the noise gate after the high-pass: the floor follows the quiet chunks only,
    speech never raises it; a chunk the voice detector called speech is never damped,
    so even a whisper passes whole"""
    n = len(chunk) // 2
    if n == 0 or voiced:
        return chunk
    vals = array('h', chunk[:n * 2])
    acc = 0
    for v in vals:
        acc += v * v
    rms = (acc / n) ** 0.5
    floor = st.get('floor')
    if floor is None or rms < floor:
        floor = rms                   # the quietest thing on tape so far is the noise itself
    else:
        floor += (rms - floor) * 0.05   # it creeps up slowly across the quiet
    st['floor'] = floor
    thr = max(st.get('gate_min', GATE_MIN), floor * st.get('gate_k', 3.0))
    if rms < thr:
        damp = DAMP_DEEP if rms < thr * 0.5 else DAMP_SOFT
        for i in range(n):
            vals[i] = int(vals[i] * damp)
        return vals.tobytes()
    return chunk


def watch_loud(chunk, st):
    """the stop-watch's doorman: a chunk goes to the decoder only when it carries
    a voice's worth of energy by the noise gate's own measure (the same thr that
    decides what vosk may hear) — a fan's hum and a room's hiss are no stop
    command, and the decode threads they would feed burn whole cores for nothing"""
    n = len(chunk) // 2
    if n == 0:
        return False
    vals = array('h', chunk[:n * 2])
    acc = 0
    for v in vals:
        acc += v * v
    rms = (acc / n) ** 0.5
    thr = max(st.get('gate_min', GATE_MIN),
              (st.get('floor') or 0) * st.get('gate_k', 3.0))
    return rms >= thr


class Detector:
    """the voice detector around vosk (webrtcvad, best effort): it counts how much speech
    passed by and how long the quiet after it has been, so the end of a phrase is heard
    from the sound itself, not guessed from vosk's own timing"""

    FRAME = 960                      # bytes: exactly 30 ms of s16 mono at 16 kHz

    def __init__(self):
        try:
            import webrtcvad
            self.vad = webrtcvad.Vad(2)
        except Exception:            # noqa: BLE001 - no vad is no problem, vosk still works
            self.vad = None
        self.buf = b''
        self.speech_ms = 0           # how much voice has passed by in total
        self.silence_ms = 0          # quiet in a row right now

    def feed(self, chunk):
        """take the cleaned chunk into the frames; True while speech is on tape"""
        self.buf += chunk
        voiced = False
        while len(self.buf) >= self.FRAME:
            frame, self.buf = self.buf[:self.FRAME], self.buf[self.FRAME:]
            said = True              # a missing detector never cuts speech short
            if self.vad:
                try:
                    said = self.vad.is_speech(frame, RATE)
                except Exception:    # noqa: BLE001
                    said = True
            if said:
                voiced = True
                self.speech_ms += 30
                self.silence_ms = 0
            elif self.speech_ms:
                self.silence_ms += 30
        return voiced


def _out(argv):
    return subprocess.run(argv, capture_output=True, text=True, timeout=3).stdout


def _pw_mics():
    """pw-cli lists every source node; its node.name is what pw-record --target wants"""
    mics, cur = [], {}
    def flush():
        if cur.get('media.class') == 'Audio/Source':
            name = cur.get('node.name', '')
            if name and not name.endswith('.monitor'):   # a loopback of an output is no ear
                mics.append({'name': name, 'label': cur.get('node.description')
                             or cur.get('node.nick') or name})
    for line in _out(['pw-cli', 'ls', 'Node']).splitlines():
        s = line.strip()
        if s.startswith('id '):
            flush()
            cur = {}
        elif '=' in s:
            k, _, v = s.partition('=')
            cur[k.strip()] = v.strip().strip('"')
    flush()
    return mics


def _pactl_mics():
    """parecord picks a source with --device, the names live in pactl"""
    mics, name, label = [], None, None
    for line in _out(['pactl', 'list', 'sources']).splitlines():
        s = line.strip()
        if s.startswith('Source #'):    # the previous source is over: keep it
            if name and not name.endswith('.monitor'):
                mics.append({'name': name, 'label': label or name})
            name, label = None, None
        elif s.startswith('Name:'):
            name = s[5:].strip()
        elif s.startswith('Description:'):
            label = s[12:].strip()
    if name and not name.endswith('.monitor'):
        mics.append({'name': name, 'label': label or name})
    return mics


def _arecord_mics():
    """arecord picks a device with -D; the cards live in arecord -l (plughw resamples for us)"""
    mics = []
    for line in _out(['arecord', '-l']).splitlines():
        m = re.match(r'card (\d+): (.+?), device (\d+): (.+)', line.strip())
        if not m:
            continue
        card, cdesc, dev, ddesc = m.groups()
        cname = re.search(r'\[([^\]]*)\]', cdesc)
        dname = re.search(r'\[([^\]]*)\]', ddesc)
        label = ': '.join(x for x in ((cname.group(1) if cname else cdesc.strip()),
                                      (dname.group(1) if dname else ddesc.split(',')[0]).strip()) if x)
        mics.append({'name': f'plughw:{card},{dev}', 'label': label})
    return mics


def list_mics():
    """every microphone the current recorder can hear: [{'name': ..., 'label': ...}], [] when none;
    the echo-cancel source is ours, not a choice: it goes on top of whatever is picked"""
    name, _cmd = recorder_cmd()
    try:
        if name == 'pw-record':
            mics = _pw_mics() if shutil.which('pw-cli') else _pactl_mics()
        elif name == 'parecord':
            mics = _pactl_mics()
        elif name == 'arecord':
            mics = _arecord_mics()
        else:
            mics = []
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    return [m for m in mics if m.get('name') != ECHO_SOURCE]


def log(msg):
    """one line into the ears' diary (/tmp/yumi/app.log): what the ears do and why"""
    print(f'[yumi-stt] {msg}', flush=True)


def oww_ear_start():
    """the trained ear «Юми» as a little process beside the daemon: the oww
    venv may be another python than the daemon's, so it lives apart — fed the
    clean sound, it answers one score per frame; None when it cannot live here
    (no venv, no model), and vosk listens as before"""
    py = os.environ.get('YUMI_OWW_PY') or str(
        Path.home() / '.local/share/yumi/oww/bin/python')
    model = os.environ.get('YUMI_OWW_MODEL') or str(
        Path.home() / '.local/share/yumi/oww/models/yumi.onnx')
    if not (os.path.isfile(py) and os.path.isfile(model)):
        return None
    helper = Path(__file__).with_name('oww_ear.py')
    try:
        proc = subprocess.Popen([py, '-u', str(helper), model],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, bufsize=0)
    except OSError as e:
        log('the trained ear will not start: ' + str(e)[-120:])
        return None
    return {'proc': proc, 'buf': bytearray(), 'in': bytearray(), 'dead': False,
            'fed': 0, 'read': 0}


_ear_lag = [0.0]               # last time the ear's backlog was complained about
_last_wake = [0.0]             # when the wake last handed over: the cooldown reads it


def oww_ear_score(ear, chunk, reset=False):
    """one frame of sound to the trained ear, its score back: b'' with reset
    only forgets what was heard; 0.0 when the ear is away, dead or too slow —
    vosk answers instead, nothing hangs on the little process"""
    if not ear or ear['dead']:
        return 0.0
    proc = ear['proc']
    if proc.poll() is not None:
        ear['dead'] = True
        return 0.0
    fed = 0
    try:
        if reset:
            proc.stdin.write(b'R')
            ear['in'] = bytearray()         # the frame on hold is forgotten too
            ear['fed'] = ear['read'] = 0
        else:
            # the chunks of the mic are not a multiple of the 80 ms frame, and
            # the whole remainder waits in `in` for the next chunk: dropping it
            # would leave holes in the stream, the melspectrogram would lose
            # its count of frames, and a spoken name would read as a murmur
            data = ear['in']
            data += chunk
            while len(data) >= OWW_CHUNK:
                proc.stdin.write(b'D' + struct.pack('<H', OWW_CHUNK)
                                 + bytes(data[:OWW_CHUNK]))
                del data[:OWW_CHUNK]
                fed += 1
            ear['in'] = data
        proc.stdin.flush()
    except (OSError, ValueError):
        ear['dead'] = True
        return 0.0
    n_frames = 1 if reset else fed          # the helper answers one line per frame
    ear['fed'] = ear.get('fed', 0) + fed
    buf = ear['buf']
    end = time.monotonic() + 0.3
    best = 0.0
    lines = 0
    while n_frames and time.monotonic() < end:
        r, _, _ = select.select([proc.stdout], [], [], 0.1)
        if not r:
            continue
        buf += os.read(proc.stdout.fileno(), 256)
        while b'\n' in buf:
            line, _, buf = buf.partition(b'\n')
            try:
                best = max(best, float(line))
            except ValueError:
                pass
            n_frames -= 1
            lines += 1
    ear['buf'] = buf
    ear['read'] = ear.get('read', 0) + lines
    lag = ear['fed'] - ear['read']
    if lag > 25 and time.monotonic() - _ear_lag[0] > 10:
        _ear_lag[0] = time.monotonic()
        log(f'the trained ear lags: {lag} frames (~{lag * 0.08:.1f} s) unread')
    return best


def ear_window_hi(hi, hi_t, score, now, keep=3.0):
    """the trained ear's best score of the last `keep` seconds: a fresh louder
    frame takes the top at once, and when the window runs out any new frame
    (even a quiet one) replaces it — so a silence after speech reads as silence"""
    if score > hi or now - hi_t > keep:
        return score, now
    return hi, hi_t


class EarStat:
    """the trained ear's memory of the last seconds, in one place: the current
    score, the best of the window, the average, how long the score stayed above
    the activation line and how many frames in a row it rose there. A candidate
    is believed when the score crosses the activation line `spike` frames in a
    row (a dip between syllables, still above the hold line, does not break the
    count) and stays believed while the score keeps above the hold line: one
    loud spike is a knock, a stretch of frames is a name, and the stretch must
    really end (below the hold line) for the door to close again.

    The scores of the different wake models are not comparable probabilities
    (nobody calibrated them against each other): these numbers only rank the
    moments inside this one model's scale, they are not shared across models."""

    def __init__(self, window=None, activation=None, hold=None, sure=None, spike=None):
        self.window = float(window or TUNE['oww_window'])
        self.activation = float(activation or TUNE['oww_threshold'])
        self.hold = float(hold or TUNE['oww_hold'])
        self.sure = float(sure or TUNE['oww_sure'])
        self.spike = int(spike or TUNE['oww_spike'])
        self.frames = deque()           # (t, score) of the last window
        self.streak = 0                 # frames in a row above the activation line
        self.active = False             # a candidate is believed right now
        self.active_t = 0.0             # when it was believed first

    def feed(self, score, now):
        """one frame's score in; the window slides, the state turns, and the
        stats dict the arbitrator reads comes back"""
        self.frames.append((now, score))
        while self.frames and now - self.frames[0][0] > self.window:
            self.frames.popleft()
        if score >= self.activation:
            self.streak += 1
        elif score < self.hold:
            self.streak = 0             # a dip between syllables (still above the hold
                                        # line) does not break the count of the stretch
        if not self.active and self.streak >= self.spike:
            self.active = True          # a stretch of frames is a name; one loud frame
            self.active_t = now         # alone is a knock, the room is full of those
        elif self.active and score < self.hold:
            self.active = False         # below the hold line: the name is over
        return self.stats(now)

    def stats(self, now=None):
        """the window in numbers: cur, hi, mean, pos_ms (the frames above the
        activation line, each worth its 80 ms), streak, active (and since when)"""
        scores = [s for _t, s in self.frames]
        hi = max(scores) if scores else 0.0
        mean = sum(scores) / len(scores) if scores else 0.0
        pos_ms = sum(1 for s in scores if s >= self.activation) * OWW_MS
        return {'cur': scores[-1] if scores else 0.0, 'hi': hi, 'mean': mean,
                'pos_ms': pos_ms, 'streak': self.streak, 'active': self.active,
                'since': self.active_t if self.active else 0.0}

    def reset(self):
        """everything forgotten: the mute ran out, her voice was on the air"""
        self.frames.clear()
        self.streak = 0
        self.active = False
        self.active_t = 0.0


def oww_wake_decision(stat):
    """the trained ear's verdict on one frame's stats: a believed candidate wakes
    her when the current frame stands on the sure line, or when the stretch's best
    stands over the threshold line — OpenWakeWord alone decides, no judge, no Vosk
    (разделы 2 и 6 ТЗ). The belief itself (a stretch of frames with its hold
    hysteresis) is EarStat's work: one loud spike alone never wakes her."""
    if not stat or not stat.get('active'):
        return False
    return stat['cur'] >= TUNE['oww_sure'] or stat['hi'] >= TUNE['oww_threshold']


_LAT = {'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo', 'ж': 'zh',
        'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o',
        'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'ts',
        'ч': 'ch', 'ш': 'sh', 'щ': 'sh', 'ы': 'y', 'ь': '', 'ъ': '', 'э': 'e', 'ю': 'yu',
        'я': 'ya'}


def variants(word):
    """spellings the two models may write for the same sound: «юми» is also «yumi»,
    «umi» (they hear ю as one u or as yu), so the wake word works with either speech language"""
    lat = ''.join(_LAT.get(c, c) for c in word)
    out = {word, lat}
    if 'yu' in lat:
        out.add(lat.replace('yu', 'u', 1))
    return out


HEARSAY = {'юми': ['юме', 'юмей', 'юный', 'юлий', 'юма', 'юмэ', 'ими', 'ями',
                   'еми', 'еле', 'уме', 'умэ', 'юмми', 'юми-и', 'джуми',
                   'yumi', 'umi', 'yume', 'emi'],
           'yumi': ['yumi', 'yume', 'youmi', 'юми', 'юме', 'юный']}
HEARSAY_FILE = HOME / 'hearsay.json'

def hearsay_load(word):
    """what the ears remember writing for this wake word: the known hear-say above
    plus everything learned from the live sessions (~/.local/share/yumi/stt/hearsay.json)"""
    try:
        learned = json.loads(HEARSAY_FILE.read_text()) or {}
    except (OSError, ValueError):
        learned = {}
    out = set(HEARSAY.get(word, []))
    out.update(w for w in learned.get(word) or [] if isinstance(w, str) and w.strip())
    return {w.lower().replace('ё', 'е') for w in out}


def hearsay_save(word, heard):
    """a spelling of the wake word the ears had never met and yet it rang true:
    remember it, so next time it is a plain match, not a guess one letter away"""
    heard = ' '.join(heard.split()).lower().replace('ё', 'е')
    if not heard or ' ' in heard or len(heard) > 12:
        return                # the spelling is one token: «ю ми» is matched by the windows
    if len(heard) < 3 or not any(near(heard, s) <= 1 for s in variants(word)):
        return                # a stray sound must not grow into the name: «на» is not «юми»
    try:
        data = json.loads(HEARSAY_FILE.read_text()) if HEARSAY_FILE.exists() else {}
    except (OSError, ValueError):
        data = {}
    data[word] = [heard] + [w for w in data.get(word) or [] if w != heard][:40]
    try:
        HEARSAY_FILE.parent.mkdir(parents=True, exist_ok=True)
        HEARSAY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    except OSError:
        pass


def near(a, b, lim=None):
    """How many letters separate the two words (the hearsay of Vosk): one letter forgiven
    to short words, two to the long ones where one slips in anyway. lim overrides that
    (the judge's wide pre-filter). Returns the edit distance, or lim+1 when it is already
    over the budget."""
    if a == b:
        return 0
    if lim is None:
        lim = 2 if min(len(a), len(b)) >= 6 else 1
    if abs(len(a) - len(b)) > lim:
        return lim + 1
    # a plain edit distance, cut short: a substitution, insertion or deletion is fine
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
            if min(cur) > lim and min(prev) > lim:
                return lim + 1
        prev = cur
    return prev[-1]


class Utterance:
    """the phrase on tape, kept for whisper to read: a little of the sound before
    the first voice (so the first word does not lose its head) and then the whole
    phrase with the quiet inside it; the room hiss before and after stays out"""

    LEAD = 6400                     # bytes: ~0.2 s of pre-speech sound on tape

    def __init__(self):
        self.lead = bytearray()
        self.buf = bytearray()
        self.lead_max = self.LEAD       # the lead's cap: the wake keeps the name's
                                        # head (a second of it on the tape), a listen
                                        # keeps a breath of the room before the voice

    def feed(self, chunk, voiced):
        """one cleaned chunk of s16 mono; True while a voice has ever passed by"""
        if voiced or self.buf:
            self.buf += chunk       # once the voice began, the inner quiet is a phrase's own
        else:
            self.lead += chunk
            if len(self.lead) > self.lead_max:
                del self.lead[:-self.lead_max]
        return self.buf

    def take(self):
        """the phrase's bytes of s16 mono (lead included), or b'' when no voice passed"""
        out = bytes(self.lead + self.buf) if self.buf else b''
        self.lead, self.buf = bytearray(), bytearray()
        return out


def wake_at(text, spellings, budget=1):
    """what came after the wake word and the spelling the ears wrote it with, or (None, None);
    whole words only, right at the start of the phrase («юми» or behind a filler
    «а можно ми»), glued hear-say like «ю и ми» counts too: «юмизнает» must not wake her.
    budget is how many letters may differ from any known spelling (0 = exact only).
    A wide budget (the judge's pre-filter) never eats a word the strict one keeps:
    «юми кто играет» must not lose «кто» to a glued «юмикто» — the strict match is
    tried first, and the wide one only catches what the strict one missed."""
    if budget > 1:
        hit, said = wake_at(text, spellings, 1)
        if hit is not None:
            return hit, said
    toks = [t.strip('.,!?"«»…:;()[]-—').lower().replace('ё', 'е')
            for t in text.split()]
    toks = [t for t in toks if t]
    for n in (3, 2, 1):                     # the fullest hear-say first, near the start:
        for i in range(0, min(len(toks), 8) - n + 1):   # a name is said first, not deep
            said = ''.join(toks[i:i + n])                # glued hear-say like «ю и ми»
            # the wide filter runs behind the judge, and it forgives one letter more
            # when the hear-say begins with the name's own letter: over music the first
            # letter is the one that survives («юноша», «юный» for «юми»), while words
            # far from the name by their first letter stay outside the strict budget
            slack = 1 if budget > 1 and any(said[:1] == s[:1] for s in spellings) else 0
            # a wide budget may not outgrow the word itself: a hear-say of three or four
            # letters keeps all but one of its letters, or half the Russian speech
            # («что», «эта», «она», «они») becomes the name and the judge reads a lesson
            # video every few seconds; long hear-says («думаю» for «джуми») still pass
            cap = budget + slack
            if len(said) <= 4:
                cap = min(cap, 1)
            dist = min(near(said, s, cap) for s in spellings)
            if dist > cap:
                continue
            # a common word one letter from the name's hear-say («иди», «или», «уже»)
            # must not wake her: it counts as the name only when a true spelling of the
            # name fits the sound no worse than the stop word does (the same cap both
            # ways, so a wide pre-filter does not let the stop words win by a shorter cap)
            if any(near(said, w, cap) < dist for w in WAKE_STOPS):
                continue
            return ' '.join(toks[i + n:]), said
    return None, None


def name_exact(text, spellings):
    """True when a full spelling of the name stands verbatim near the start of the
    phrase: vosk heard it letter for letter, no judge is needed to believe it"""
    toks = [t.strip('.,!?"«»…:;()[]-—').lower().replace('ё', 'е')
            for t in text.split()]
    toks = [t for t in toks if t]
    known = set(spellings)
    for n in (3, 2, 1):                     # the same glued hear-says wake_at counts
        for i in range(0, min(len(toks), 8) - n + 1):
            if ''.join(toks[i:i + n]) in known:
                return True
    return False


WAKE_STOPS = {'иди', 'или', 'уже', 'ели', 'юли', 'умер', 'они'}    # the ears' false names: never wake on these
STOP_COMMANDS = {'стоп', 'стопи', 'останови', 'остановись', 'прекрати', 'stop'}
STOP_FILE = HOME / 'stop_words.json'       # extra stop words the user may add (a json list)
_stops_cache = {'t': 0.0, 'words': frozenset(STOP_COMMANDS)}


def _stop_words():
    """her stop commands plus the ones the user added on disk (reread at most twice a second)"""
    now = time.monotonic()
    if now - _stops_cache['t'] > 5.0:
        words = set(STOP_COMMANDS)
        try:
            extra = json.loads(STOP_FILE.read_text())
            words.update(w.lower().replace('ё', 'е').strip()
                         for w in (extra if isinstance(extra, list) else []) if isinstance(w, str))
        except (OSError, ValueError):
            pass
        _stops_cache.update(t=now, words=words)
    return _stops_cache['words']


def stop_at(text):
    """True when a whole word of the phrase is one of her stop commands: «стоп»
    overrides everything she is busy with, even her own voice mid-word. A word one
    letter from a short command counts too: over the playing music the ears slip
    a letter («стоп» comes back as «стол», «стой»), and a shout that must stop her
    is not lost to one slip"""
    words = _stop_words()
    toks = [t.strip('.,!?"«»…:;()[]-—').lower().replace('ё', 'е')
            for t in (text or '').split()]
    return any(t in words or (len(t) >= 4 and any(near(t, w) <= 1
               for w in words if len(w) <= 6)) for t in toks if t)


def stop_shout(text, names=()):
    """the phrase is a stop and nothing but a stop (her name may lead the shout):
    while her own voice plays, the words the ears pick up may be her own, so a
    stop through the closed ears counts only when the whole phrase is one —
    her «останови» inside a longer answer must never stop her mid-word"""
    toks = [t.strip('.,!?"«»…:;()[]-—').lower().replace('ё', 'е')
            for t in (text or '').split()]
    toks = [t for t in toks if t]
    if toks and names and (toks[0] in names
                           or any(near(toks[0], n, 1) <= 1 for n in names)):
        toks = toks[1:]                 # «юми» (or its hear-say) may lead the shout
    words = _stop_words()
    return bool(toks) and all(
        t in words or (len(t) >= 4 and any(near(t, w) <= 1
                     for w in words if len(w) <= 6)) or stop_mush(t, names)
        for t in toks)


def stop_mush(text, spellings=()):
    """the stop may arrive glued to her name («юмистоп» for «юми стоп», the pause
    drowns in the music and the ear writes one word): a word counts when it ends
    in a stop command and what stands before it is her name as the ears spell
    it — «стопка» stays a box, «приостанови» stays a word. The trained ear also
    has its latin quirks on the noise («umistop», «mistop»): a word ending in
    the english 'stop' with a short head counts too"""
    words = _stop_words()
    for tok in (t.strip('.,!?"«»…:;()[]-—').lower().replace('ё', 'е')
                for t in (text or '').split()):
        if not tok or tok in words:
            continue
        for w in (x for x in words if len(x) >= 4):
            if tok.endswith(w):
                head = tok[:-len(w)]
                if head and (head in spellings
                             or any(near(head, s, 1) <= 1 for s in spellings)):
                    return True
        if tok.endswith('stop') and 2 <= len(tok) - 4 <= 4:
            return True                 # «umistop», «mistop»: the ear's latin mush
        for s in spellings:
            if near(tok, s + 'стоп', 2) <= 2:
                return True             # «юмистоп»: the pause drowned, one word stands
    return False


_PHON_MAP = str.maketrans({'ю': 'у', 'ё': 'о', 'ы': 'и', 'э': 'е',
                           'б': 'п', 'д': 'т', 'г': 'к', 'з': 'с', 'ж': 'ш',
                           'в': 'ф', 'щ': 'ш', 'ц': 'т',
                           'ь': '', 'ъ': '', 'й': ''})


def phon_key(word):
    """how a careless ear spells the word, one sound to a class: the vowels slip
    (ю heard as у, и as ы, э as е), the voiced consonants hush into their quiet
    twins (б into п, д into т), the soft signs and the glides go, and the
    doubled letters fold into one («юмми» sounds as «юми»). Not a distance, a
    spelling of the sound itself: the slips a half-heard name suffers collapse
    into the same key, so «дюми», «уми» and «юмы» all ring as «уми»"""
    out = word.translate(_PHON_MAP)
    return ''.join(c for i, c in enumerate(out) if i == 0 or c != out[i - 1])


def phon_candidate(text, spellings):
    """a candidate the letter budget missed but the sound keeps: a token near
    the start of the phrase whose careless-ear key rings as the name's own key.
    (after, said) like wake_at answers, or (None, None); a stop word's key never
    counts («иди» and «уже» do not dress up as the name) and the token must have
    three letters at least — the short ones keep their letter budget instead"""
    keys = {phon_key(s): s for s in spellings if len(s) >= 3}
    stops = {phon_key(w) for w in WAKE_STOPS}
    toks = [t.strip('.,!?"«»…:;()[]-—').lower().replace('ё', 'е')
            for t in text.split()]
    toks = [t for t in toks if t]
    for n in (3, 2, 1):                     # the fullest hear-say first, near the start
        for i in range(0, min(len(toks), 4) - n + 1):
            said = ''.join(toks[i:i + n])
            if len(said) < 3:
                continue
            key = phon_key(said)
            if key in stops or key not in keys:
                continue
            return ' '.join(toks[i + n:]), said
    return None, None


class Arbiter:
    """the middle mind between the ears and the judge: it weighs the trained
    ear's window, the voice detector and vosk's letters, and says which way
    every half-heard moment goes — past everything at once (FAST_WAKE), to the
    judge for a reading (SEND_JUDGE) or nowhere (IGNORE). It replaces nobody:
    the ears keep hearing as they did, it only decides who is worth the judge's
    seconds. A deaf trained ear alone is no veto any more, only a lower rank:
    a name-shaped word another ear wrote still opens the way. It also holds the
    judge's doorman's keys: a rate window between reads, and no fragment sent
    twice — the same words on the same sound are not read twice over."""

    def __init__(self, rate=None, ttl=None):
        self.rate = float(rate or TUNE['judge_rate'])
        self.ttl = float(ttl or TUNE['judge_ttl'])
        self.last_t = 0.0               # when the judge was last sent to
        self.last_fp = None             # (said, tape fingerprint) of the last fragment sent
        self.sent = 0                   # fragments that went to the judge
        self.held = 0                   # candidates the doorman held back (rate, busy, same)
        self.ignored = 0                # moments with no signs at all

    def decide(self, stat, exact, cand, phon, vad, close=None):
        """(action, reason) for this moment. The trained ear speaks first (its
        score is its rank in its own model's scale), then what the other ears
        heard: exact = vosk spelled the name letter for letter, cand = a name
        shaped word within the wake budget, phon = the same only by sound, close
        = the word stands one letter from a known spelling (a far mush of three
        letters' distance is not a confident candidate, it is ordinary speech),
        vad = a voice was on the air. The old absolute veto lives here as a rank
        only: below the deaf line the way is still open for a name-shaped word
        with a voice behind it — what stays closed is the way for anything else."""
        if stat is not None and hasattr(stat, 'stats'):
            stat = stat.stats()             # the ear's object, or the dict it answers
        hi = stat['hi'] if stat else 0.0
        cur = stat.get('cur', hi) if stat else 0.0
        streak = stat.get('streak', 0) if stat else 0
        if exact:
            return ARB_FAST, 'name_exact'
        # the sure line wakes her at once, but only in a stretch: one loud frame
        # in a row is what a video's voice does too, and a judge's second costs
        # less than an answer to nobody (the stretch is the ear's own hysteresis)
        if streak >= TUNE['oww_spike'] and cur >= TUNE['oww_sure']:
            return ARB_FAST, 'ear_sure'
        if stat and stat['active'] and hi >= TUNE['oww_threshold']:
            return ARB_JUDGE, 'ear_strong'      # 0.5–0.7, held by the hysteresis: ask the judge
        if hi >= TUNE['oww_veto']:
            if cand and vad:
                return ARB_JUDGE, 'ear_low_cand'    # 0.2–0.5, a voice, a near-name
            return ARB_IGNORE, 'ear_low'
        if phon:
            return ARB_JUDGE, 'phonetic'            # a deaf ear is no veto for a name-shaped sound
        if cand and close and vad:
            return ARB_JUDGE, 'vosk_close'
        return ARB_IGNORE, 'deaf'

    def may_send(self, said, fragment, now, busy):
        """True when the judge may read this fragment now: nothing of his is
        still on the table (busy), the rate window has passed, and the fragment
        is not the very one he already read (the words and the sound together
        are the fragment's fingerprint). A send is booked here, so the caller
        needs no second bookkeeping."""
        if busy:
            self.held += 1
            return False, 'busy'
        if now - self.last_t < self.rate:
            self.held += 1
            return False, 'rate'
        fp = (said or '', hashlib.md5(fragment).hexdigest())
        if fp == self.last_fp:
            self.held += 1
            return False, 'same'
        self.last_t, self.last_fp = now, fp
        self.sent += 1
        return True, 'ok'


class Diag:
    """the wake's book of doubts (/tmp/yumi/wake.jsonl): one JSON line for every
    moment a candidate appeared — what each ear said, where the arbitrator sent
    it and why, what the judge answered and how the story ended. It is what the
    wake metrics read (tools/wake_metrics.py), and the only place the reasons
    of a lost wake can be found after the fact."""

    def __init__(self, on):
        self.on = bool(on)

    def event(self, **fields):
        if not self.on:
            return
        fields['timestamp'] = time.strftime('%Y-%m-%dT%H:%M:%S')
        try:
            TAP_DIR.mkdir(parents=True, exist_ok=True)
            with open(TAP_DIR / 'wake.jsonl', 'a', encoding='utf-8') as f:
                f.write(json.dumps(fields, ensure_ascii=False) + '\n')
        except OSError:
            pass


def judge_wav(fragment):
    """the judge's fragment on the shelf (/tmp/yumi/judge-*.wav), when the debug
    wavs are asked for (stt_diag_wav in the settings): the sound the judge read,
    the few newest of them stay — it is a debug mode, not a diary"""
    if not TUNE['stt_diag_wav'] or not fragment:
        return
    try:
        TAP_DIR.mkdir(parents=True, exist_ok=True)
        with wave.open(str(TAP_DIR / time.strftime('judge-%Y%m%d-%H%M%S.wav')), 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(fragment)
        for old in sorted(TAP_DIR.glob('judge-*.wav'))[:-5]:
            old.unlink()
    except OSError:
        pass


def wake_feed_wav(tape):
    """what the trained ear was fed through the whole wake, on the shelf
    (/tmp/yumi/wake_feed.wav, one file, rewritten every wake) when stt_diag_wav
    asks: with it a deaf ear is a measurement, not a guess"""
    if not tape:
        return
    try:
        TAP_DIR.mkdir(parents=True, exist_ok=True)
        with wave.open(str(TAP_DIR / 'wake_feed.wav'), 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(bytes(tape))
    except OSError:
        pass


_judge = threading.Lock()   # one whisper reading at a time, wake to wake


def transcribe_model(model, pcm, lang, hotwords=None, beam=1):
    """s16 mono through a whisper that is already in hand ('' for silence): a function
    of its own so a thread may read with a model the lock will not hand out again
    (an RLock is open to its own thread only). hotwords leans the reading toward
    the wake word: the small whisper keeps dropping a short name said into the
    room — «юми» comes back as «умень» or not at all. beam: a wider beam guesses a
    tangled phrase better (the phrase reads take 5, the wake judge stays at 1 —
    its answer is wanted fast, and a name is not a tangled phrase)"""
    import numpy as np
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    try:
        segments, _info = model.transcribe(
            audio, language=lang or None, beam_size=beam, vad_filter=True,
            condition_on_previous_text=False, hotwords=hotwords)
    except TypeError:           # an older faster-whisper knows no hotwords
        segments, _info = model.transcribe(
            audio, language=lang or None, beam_size=beam, vad_filter=True,
            condition_on_previous_text=False)
    return ' '.join(s.text for s in segments).strip()


def transcribe_nemo_model(rec, pcm, lang):
    """s16 mono RATE through the streaming Nemotron transducer ('' for silence, None
    when the reading fails): a streaming reader, so the whole tape goes in as if it
    had just been heard, and the final text comes out once the sound is over"""
    import numpy as np
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    stream = rec.create_stream()
    try:
        stream.set_option('language', lang if lang in ('ru', 'en') else 'auto')
    except (TypeError, ValueError, RuntimeError):
        try:
            stream.set_option(language=lang)
        except Exception:               # noqa: BLE001 - the model reads on without it
            pass
    stream.accept_waveform(RATE, audio)
    stream.input_finished()
    while rec.is_ready(stream):
        rec.decode_stream(stream)
    try:
        text = json.loads(rec.get_result_as_json_string(stream)).get('text', '')
    except Exception:                   # noqa: BLE001 - a plain result is a fine fallback
        res = rec.get_result(stream)
        text = res[0] if isinstance(res, (tuple, list)) and res else str(res)
    return ' '.join(str(text).split())


class Ears:
    """downloads the models one by one and answers one listen at a time"""

    def __init__(self):
        self.models = {}
        self.wsp = {}                   # faster-whisper models, one per (lang, size)
        self.nemo = None                # the streaming Nemotron ear, loaded on the first need
        self.error = None
        self.busy = False
        self.abort = False
        self.kind = None                # what the lock holds now: 'listen' or 'wake'
        self.dl = None                  # a model's name while it downloads: the bubble's fuel
        # RLock: a listen holds it and still reads the whisper loader within itself
        self.lock = threading.RLock()
        self.tap = Tap(self)

    def model(self, lang, size='small'):
        """the recognizer's factory: the model folder, downloaded on the first need;
        one model per language is kept in mind — a size switch lets the old one go"""
        from vosk import Model
        with self.lock:
            key = (lang, size if size in MODELS.get(lang, MODELS['ru']) else 'small')
            if key in self.models:
                return self.models[key]
            zip_name, folder = MODELS[key[0]][key[1]]
            path = HOME / folder
            if not path.exists():
                self.dl = f'vosk {lang} (~45 MB)'
                try:
                    download(zip_name, folder)
                finally:
                    self.dl = None
            m = Model(str(path))
            # no eviction of the same-language twin: the tap wants the small model while
            # the wake wants the big one, and throwing the other out made the two reload
            # each other forever (a big model is ~1.9 GB, and under swap that jams the
            # lock everything else waits on); both stay, they fit in memory together
            self.models[key] = m
            return m

    def whisper(self, lang, size='small'):
        """the faster-whisper model for a language, downloaded and loaded on the first
        need; one model is kept per language — a size switch lets the old one go"""
        from faster_whisper import WhisperModel
        with self.lock:
            name = WHISPER_SIZE.get(size if size in WHISPER_SIZE else 'small')
            key = (lang, name)
            if key in self.wsp:
                return self.wsp[key]
            path = HOME / 'whisper'      # the models live with the rest of the ears
            path.mkdir(parents=True, exist_ok=True)
            log(f'whisper: loading {name} ({lang})...')
            if not (path / ('models--Systran--faster-whisper-' + name)).exists():
                size = '~460 MB' if name == 'small' else '~1.5 GB'
                self.dl = f'whisper {name} ({lang}, {size})'
            try:
                # cpu_threads: without it the judge takes a thread per core and grabs
                # the whole laptop for seconds on every near-miss; two read the tape
                # just as fast per watt
                m = WhisperModel(name, device='cpu', compute_type='int8',
                                 cpu_threads=2, download_root=str(path))
            finally:
                self.dl = None
            for other in [k for k in self.wsp if k[0] == lang and k != key]:
                del self.wsp[other]
            self.wsp[key] = m
            log(f'whisper: {name} is ready')
            return m

    def nemotron(self):
        """the streaming Nemotron 0.6b transducer, or None: the ear the settings may
        name (stt_engine: 'nemotron', tools/nemotron_setup.sh puts it in); loaded once
        and shared by every language (the language is set on the stream itself)"""
        if not (NEMO_DIR / 'encoder.int8.onnx').exists():
            return None
        import sherpa_onnx
        with self.lock:
            if self.nemo is not None:
                return self.nemo
            try:
                self.nemo = sherpa_onnx.OnlineRecognizer.from_transducer(
                    tokens=str(NEMO_DIR / 'tokens.txt'),
                    encoder=str(NEMO_DIR / 'encoder.int8.onnx'),
                    decoder=str(NEMO_DIR / 'decoder.int8.onnx'),
                    joiner=str(NEMO_DIR / 'joiner.int8.onnx'),
                    num_threads=2, sample_rate=RATE, feature_dim=80,
                    decoding_method='greedy_search')
            except Exception as e:      # noqa: BLE001 - a failed ear is no tragedy
                log('nemotron will not load, whisper reads instead: ' + str(e)[-120:])
                return None
            log('nemotron: the 0.6b ear is ready')
        return self.nemo

    def transcribe(self, pcm, lang, size='small', beam=5):
        """bytes of s16 mono RATE through faster-whisper: the words it hears ('' for
        silence). vad_filter keeps the quiet out (whisper dreams on silence), and the
        chain is not carried on (so one dream does not write the next); beam 5 reads
        a tangled phrase better than the judge's quick beam 1"""
        return transcribe_model(self.whisper(lang, size), pcm, lang, beam=beam)

    def transcribe_said(self, said, lang, size='small', engine='whisper'):
        """the phrase's bytes (s16 mono RATE) through the engine the settings name:
        nemotron or whisper read it; None when neither would — vosk's own words are
        the caller's business. One place for listen() and wake() to read the tape."""
        text = None
        if engine == 'nemotron':
            nrec = self.nemotron()
            if nrec:                    # the streaming ear reads; when it fails,
                try:                    # vosk's own words answer — whisper stays out
                    text = transcribe_nemo_model(nrec, said, lang)
                except Exception as e:  # noqa: BLE001 - it is best effort
                    log('nemotron failed, vosk answers instead: ' + str(e)[-120:])
        else:                           # the whisper ear (the settings name it) reads
            try:
                text = self.transcribe(said, lang, size)   # whisper reads the tape
            except Exception as e:      # noqa: BLE001 - whisper is best effort
                log('whisper failed, vosk answers instead: ' + str(e)[-120:])
        return text

    def listen(self, lang, secs, quiet, size='small', mic=None, ear=None, engine='whisper'):
        """hear one phrase: the recognized text ('' when there was nothing)"""
        from vosk import KaldiRecognizer
        import selectors
        echo = echo_ready(mic)
        if echo and not (shutil.which('pw-record') or shutil.which('parecord')):
            echo = None                 # arecord cannot aim at a PipeWire source: plain mic then
        name, cmd = recorder_cmd(echo or mic)
        if not name:
            raise RuntimeError('no parecord, pw-record or arecord for the microphone')
        # vosk only hears where the phrase ends here (whisper reads the words):
        # the small model is enough for that, the big one is not worth its gigabytes
        model = self.model(lang, 'small')
        with self.lock:                     # one listen at a time
            if self.busy:
                return None                 # the app never does this, but just in case
            self.busy, self.abort = True, False
            proc = None                     # the recorder may never be born: the finally checks
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except OSError as e:
                self.busy = False
                raise RuntimeError(f'the microphone would not start: {e}')
            try:
                sel = selectors.DefaultSelector()
                sel.register(proc.stdout, selectors.EVENT_READ)
                rec = KaldiRecognizer(model, RATE)
                rec.SetWords(False)
                det = Detector()
                utt = Utterance()           # the phrase on tape, for whisper to read
                gmin, gk = ear_gate(ear)
                st = {'hp': 0.0, 'floor': None, 'gate_min': gmin, 'gate_k': gk}
                t0 = time.monotonic()
                heard = False               # at least one byte of sound has arrived
                while time.monotonic() - t0 < secs:
                    if self.abort:
                        break
                    if not sel.select(1.0):               # nothing to read for a second
                        if not heard and time.monotonic() - t0 > quiet:
                            # the recorder hangs around but gives not a byte: a setup
                            # problem (parecord on some desktops), not silence
                            raise RuntimeError(f'{name} gave no sound')
                        continue
                    chunk = os.read(proc.stdout.fileno(), 4000)     # ~0.125 s of audio
                    if not chunk:
                        # the recorder is gone: a clean exit is the phrase end, a crash
                        # is worth its last words in the error
                        die, err = proc.poll(), ''
                        if die:
                            try:
                                err = ' ' + proc.stderr.read().decode(errors='replace')[-200:]
                            except OSError:
                                pass
                        if die not in (None, 0):
                            raise RuntimeError(f'{name} died ({die}){err}'.rstrip())
                        break
                    heard = True
                    if muted():
                        continue        # she is speaking: the ears are closed
                    chunk = hp_pass(chunk, st)            # hum and rumble go
                    voiced = det.feed(chunk)              # the voice detector follows the frames
                    clean = gate_noise(chunk, st, voiced) # the hiss dies for vosk's own ear
                    utt.feed(chunk, voiced)               # the phrase collects on the tape whole:
                                                          # the gate never touches it, so the quiet
                                                          # endings of words reach the reader intact
                    if rec.AcceptWaveform(clean):         # vosk heard the phrase end
                        break
                    # a click or a knock passes the detector for a moment: a breath of
                    # speech (under 0.4 s) is not yet a phrase, so the quiet after it
                    # does not end the listen — the real words may still be coming
                    if det.speech_ms >= 400 and det.silence_ms >= VAD_TAIL:
                        break                             # the voice died down: the phrase is over
                    if time.monotonic() - t0 > quiet and not json.loads(
                            rec.PartialResult() or '{}').get('partial'):
                        break                             # nothing but quiet: do not wait
                said = utt.take()
                text = ''
                vtext = ' '.join(json.loads(rec.FinalResult() or '{}').get('text', '').split())
                if len(said) >= RATE:         # under a second is a breath, not a phrase
                    text = self.transcribe_said(said, lang, size, engine)
                if not text:                  # whisper silent or away: vosk's words stand in
                    text = vtext
                return {'text': text, 'vosk': vtext}   # both readings: the mind will choose
            finally:
                if proc is not None:            # the recorder was born: it dies with the call
                    try:
                        proc.terminate()
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                    except OSError:
                        pass
                    finally:
                        try:
                            proc.stdout.close()
                        except OSError:
                            pass
                        try:
                            proc.stderr.close()
                        except OSError:
                            pass
                self.busy = False

    def wake(self, lang, word, secs, size='small', mic=None, ear=None,
             sensitivity='normal', confirm=True, model_size=None, judge_log=False,
             engine='whisper', oww_ear='auto', diag=False):
        """wait for the wake word, keeping the microphone on:
        {'woke': True, 'text': the phrase after it} — the name was heard, the phrase came
        {'woke': True, 'text': ''} — the name alone, a phrase may still come
        {'woke': False, 'stop': True, ...} — «стоп» was heard: everything stops now
        {'woke': False, ...} — quiet the whole 'secs', or called off by /stop, or busy.
        OpenWakeWord with yumi.onnx is the only ear that decides (раздел 2 ТЗ) — Vosk,
        Nemotron and Whisper stay out of the wake decision: vosk runs as the light
        independent «стоп» channel (it works in every state, раздел 9), and after the
        name the phrase is heard out by the VAD endpointing and read by the configured
        STT (раздел 7), with the phrase's head restored from the ring buffer."""
        import selectors
        from vosk import KaldiRecognizer
        echo = echo_ready(mic)
        if echo and not (shutil.which('pw-record') or shutil.which('parecord')):
            echo = None                 # arecord cannot aim at a PipeWire source: plain mic then
        name, cmd = recorder_cmd(echo or mic)
        if not name:
            raise RuntimeError('no parecord, pw-record or arecord for the microphone')
        word = ' '.join(str(word).split()).lower() or 'юми'
        diag = Diag(diag or judge_log)      # the book of doubts: diag=1 or the diary switch
        spellings = variants(word) | hearsay_load(word)    # the stop's glued heads («юмистоп»)
        if self.busy:                       # a wake or a listen already owns the mic:
            return {'woke': False, 'text': None}   # answer at once, before anything runs

        # the ring of the last ~8 s of clean sound: the phrase's head (the name's
        # own beginning among it) goes to STT from here after the wake (раздел 10)
        ring = bytearray()
        RING_MAX = RATE * 8 * 2          # 8 s of s16 mono

        def wake_result(text, why):
            """the wake's answer and its diary line: the time from the first voice
            on the air to this moment is the latency the metrics read"""
            latency = round((time.monotonic() - voice_t0) * 1000.0) if voice_t0 else None
            diag.event(arbitration='WAKE', reason=why, wake_candidate=True,
                       judge_called=False, final_wake=True, latency_ms=latency)
            return {'woke': True, 'text': text}

        def judge_note(what):
            """one line into the wake's diary (judge.log): what the ear heard and why
            it woke or stayed quiet — without it a failed wake is a guess. The judge
            itself is gone (OpenWakeWord alone decides), the diary stays."""
            if not judge_log:
                return
            TAP_DIR.mkdir(parents=True, exist_ok=True)
            try:
                with open(TAP_DIR / 'judge.log', 'a', encoding='utf-8') as f:
                    f.write(f'{time.strftime("%H:%M:%S")} {what}\n')
            except OSError:
                pass

        def diag_base(state, voiced=False):
            """the fields every diary line carries (раздел 11 ТЗ): the state
            (SLEEP/LISTENING/SPEAKING), whether the VAD hears a voice now, whether
            her own voice is on the air (the mute) and whether the AEC is on"""
            return {'state': state, 'vad_active': bool(voiced),
                    'wake_threshold': TUNE['oww_threshold'],
                    'tts_active': muted(), 'aec_active': bool(_echo['on'])}

        def wake_latency():
            """ms from the first voice on the air to now: the wake's latency"""
            return round((time.monotonic() - voice_t0) * 1000.0) if voice_t0 else None

        def wake_result(text, why, score=0.0):
            """the wake's answer and its diary line"""
            diag.event(arbitration='WAKE', reason=why, wake_candidate=True,
                       wake_detected=True, wake_score=round(score, 4),
                       wake_latency_ms=wake_latency(),
                       buffer_duration_ms=round(len(ring) / (RATE * 2.0), 2),
                       judge_called=False, final_wake=True,
                       **diag_base('LISTENING'))
            return {'woke': True, 'text': text}

        def stop_result(text, state):
            """the stop's answer and its diary line: «стоп» overrides everything,
            and the record of when it happened is part of the wake's story"""
            diag.event(arbitration='STOP', reason='stop_command', vosk_text=text,
                       judge_called=False, final_wake=False, **diag_base(state))
            judge_note(f'STOP {text!r}')
            return {'woke': False, 'stop': True, 'text': text}
        with self.lock:                     # one microphone at a time
            if self.busy:
                return {'woke': False, 'text': None}
            self.busy, self.abort, self.kind = True, False, 'wake'
            proc = None                     # the recorder may never be born: the finally checks
            diag_wav = bool(TUNE['stt_diag_wav']) or bool(diag)
            ear_tape = bytearray() if diag_wav else None
            ear_oww = None                  # the trained ear is born inside, dies in the finally
            voiced = False                  # the last chunk had a voice on it
            try:
                # the recorder is born first: whatever fails after it is cleaned
                # up by the finally below. The trained ear is born after the busy
                # check, inside the lock — born on a busy path it would never reach
                # the finally and would haunt the RAM as an orphan (a new ~190 MB
                # ear every call that found the wake busy — the oom killer's
                # dinner). It dies with this wake, a new one is born on the next.
                try:
                    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                except OSError as e:
                    raise RuntimeError(f'the microphone would not start: {e}') from e
                if oww_ear in ('auto', 'oww'):
                    ear_oww = oww_ear_start()
                    if oww_ear == 'oww' and ear_oww is None:
                        log('the trained ear is not here: wake will not hear the name')
                    elif ear_oww is not None:
                        log('the trained ear «Юми» is on duty: OpenWakeWord alone decides')
                sel = selectors.DefaultSelector()
                sel.register(proc.stdout, selectors.EVENT_READ)
                try:
                    # the stop ear: the small vosk reads the cleaned sound as it
                    # comes, so «стоп» works in every state (раздел 9 ТЗ) — the
                    # name decision never touches it
                    rec = KaldiRecognizer(self.model(lang, 'small'), RATE)
                    rec.SetWords(False)
                except Exception as e:      # noqa: BLE001 - no vosk: no stop channel
                    log('the stop ear will not start: ' + str(e)[-120:])
                    rec = None
                det = Detector()            # one detector for the whole window: its state lives
                gmin, gk = ear_gate(ear)
                st = {'hp': 0.0, 'floor': None, 'gate_min': gmin, 'gate_k': gk}  # cleaner's memory
                t0 = time.monotonic()
                heard = False               # at least one byte of sound has arrived
                last_chunk = t0             # when the recorder last gave a byte
                respawns = 0                # how many times the recorder restarted
                was_muted = False           # her voice was on the air when the last chunk came
                woken = False               # the name has passed by: LISTENING now
                wake_t = 0.0                # when the name was believed
                wake_score = 0.0            # the ear's best score of the wake
                voice_t0 = 0.0              # when a voice first came: the wake latency
                was_voiced = False          # a voice was on the last chunk
                phrase_ms = 0.0             # speech since the name: the phrase gathers
                heard_after = ''            # the old ear's words past the name (vosk fallback)
                ear_stat = EarStat()        # the trained ear's hysteresis window
                utt = None                  # the phrase on tape, LISTENING only
                ear_note = [0.0]            # when the ear's last loud frame was noted
                cool_note = [False]         # the cooldown logged once a window, not a storm
                judge_note(f'WAKE open {secs:.0f} s (OpenWakeWord only)')
                while time.monotonic() - t0 < secs:
                    if self.abort:
                        break               # /stop, or a listen that needs the microphone
                    if not sel.select(0.5):
                        if not heard and time.monotonic() - t0 > 8:
                            raise RuntimeError(f'{name} gave no sound')
                        if woken and phrase_ms == 0 and \
                                time.monotonic() - wake_t > WAKE_AFTER:
                            # the name passed by, nothing followed it: her turn to greet
                            return wake_result('', 'name_alone', wake_score)
                        if woken and time.monotonic() - wake_t > TUNE['wake_listen']:
                            break           # the phrase ran long: hand out what has gathered
                        # the recorder went dumb mid-window (a source yanked away, a
                        # driver's sulks): the ears must not wait out the whole wake
                        # deaf — the recorder starts over, the stop ear and the
                        # detector with it, and whatever the gap tore goes
                        if heard and time.monotonic() - last_chunk > 6:
                            proc.terminate()
                            try:
                                proc.wait(timeout=2)
                            except subprocess.TimeoutExpired:
                                proc.kill()
                            respawns += 1
                            if respawns > 3:
                                raise RuntimeError(f'{name} keeps going dumb')
                            log(f'the microphone went dumb, {name} starts again ({respawns})')
                            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                                    stderr=subprocess.PIPE)
                            sel.register(proc.stdout, selectors.EVENT_READ)
                            last_chunk = time.monotonic()
                            if rec is not None:
                                rec = KaldiRecognizer(self.model(lang, 'small'), RATE)
                                rec.SetWords(False)
                            det = Detector()
                            del ring[:]             # the gap tore the stream: a stitched
                                                    # phrase would read as a dream
                            if ear_oww is not None:
                                oww_ear_score(ear_oww, b'', reset=True)   # the ear forgets it too
                            ear_stat.reset()
                            utt = None
                            voice_t0 = 0.0
                            was_voiced = False
                        continue
                    chunk = os.read(proc.stdout.fileno(), 4000)     # ~0.125 s of audio
                    last_chunk = time.monotonic()
                    if not chunk:
                        die, err = proc.poll(), ''
                        if die:
                            try:
                                err = ' ' + proc.stderr.read().decode(errors='replace')[-200:]
                            except OSError:
                                pass
                        if die not in (None, 0):
                            raise RuntimeError(f'{name} died ({die}){err}'.rstrip())
                        break               # the stream is over: what is on the tape stands
                    heard = True
                    clean = hp_pass(chunk, st)          # hum and rumble go
                    voiced = det.feed(clean)            # the voice detector follows the frames
                    if voiced and not was_voiced and not voice_t0:
                        voice_t0 = time.monotonic()     # a voice began: the latency clock runs
                    was_voiced = voiced
                    chunk = gate_noise(clean, st, voiced)   # the hiss dies, speech is kept
                    if muted():
                        # she is speaking: the ears are shut to her own voice — the
                        # trained ear, the ring and the tape do not see it. But «стоп»
                        # must cut her off mid-word: the stop ear keeps running, and
                        # through the closed ears only a stop and nothing else counts
                        if rec is not None and rec.AcceptWaveform(chunk):
                            words = ' '.join(json.loads(
                                rec.FinalResult() or '{}').get('text', '').split())
                            if stop_shout(words, spellings):
                                return stop_result(words, 'SPEAKING')
                        was_muted = True
                        continue
                    if was_muted:               # the mute ran out mid-window: her voice
                        was_muted = False       # stays out of the stop ear, the ring,
                        if rec is not None:     # the detector and the ear's memory
                            rec = KaldiRecognizer(self.model(lang, 'small'), RATE)
                            rec.SetWords(False)
                        det = Detector()
                        del ring[:]             # the buffer held only her voice: it goes
                        if ear_oww is not None:
                            oww_ear_score(ear_oww, b'', reset=True)   # the ear forgets her voice too
                        ear_stat.reset()
                        utt = None
                        voice_t0 = 0.0
                        was_voiced = False
                    # the stop ear: «стоп» overrides everything, wake word or not —
                    # in SLEEP a stop word in the passing speech stops her (as it
                    # always did), in LISTENING only a phrase that is a stop and
                    # nothing else does («останови музыку» is a command: it goes
                    # to STT, not to the stop)
                    if rec is not None:
                        if rec.AcceptWaveform(chunk):
                            words = ' '.join(json.loads(
                                rec.FinalResult() or '{}').get('text', '').split())
                            self.tap.note(words)    # the tap's diary takes the wake's phrases
                        else:
                            words = json.loads(rec.PartialResult() or '{}').get('partial', '')
                        if stop_at(words) or stop_mush(words, spellings):
                            if not woken or stop_shout(words, spellings):
                                return stop_result(words, 'LISTENING' if woken else 'SLEEP')
                    else:
                        words = ''
                    # the ring: every clean chunk lands here (not only the loud
                    # ones — the phrase's head must be whole for STT)
                    ring += clean
                    if len(ring) > RING_MAX:
                        del ring[:-RING_MAX]
                    if ear_tape is not None:
                        ear_tape += clean
                    # the trained ear, SLEEP only: once the name is believed its
                    # work is done, the phrase is nobody's but the user's
                    if ear_oww is not None and not woken:
                        score = oww_ear_score(ear_oww, clean if TUNE['oww_stream_raw'] else chunk)
                        if judge_log and score >= 0.1 and \
                                time.monotonic() - ear_note[0] > 0.5:
                            ear_note[0] = time.monotonic()
                            judge_note(f'EAR {score:.2f}')
                        stat = ear_stat.feed(score, time.monotonic())
                        if stat['active']:
                            # a wake just handed over: the same name still ringing on
                            # the air must not wake her twice (cooldown, configurable)
                            if time.monotonic() - _last_wake[0] < TUNE['oww_cooldown']:
                                if not cool_note[0]:
                                    cool_note[0] = True
                                    judge_note(f'WAKE held by cooldown (hi={stat["hi"]:.2f})')
                            elif oww_wake_decision(stat):
                                # the name is believed: OpenWakeWord alone decided
                                woken = True
                                wake_t = time.monotonic()
                                wake_score = stat['hi']
                                _last_wake[0] = wake_t
                                judge_note(f'WAKE oww (hi={stat["hi"]:.2f}, '
                                           f'cur={stat["cur"]:.2f})')
                                diag.event(arbitration='CANDIDATE', reason='oww_stretch',
                                           wake_candidate=True,
                                           wake_score=round(stat['hi'], 4),
                                           judge_called=False, final_wake=False,
                                           **diag_base('LISTENING', voiced))
                                # the phrase's head comes off the ring: OpenWakeWord
                                # answers a frame or two INTO the name, the head must
                                # be on the tape when STT reads it
                                lead = int(TUNE['wake_lead_ms'] * RATE * 2 / 1000.0)
                                utt = Utterance()
                                utt.lead = bytearray(ring[-lead:]) if lead else bytearray()
                                utt.lead_max = max(utt.LEAD, len(utt.lead))
                                ear_stat.reset()    # the window starts over with her listening
                                det = Detector()    # the phrase's own count of speech and quiet
                                phrase_ms = 0.0
                    elif ear_oww is None and not woken and words:
                        # no trained ear (wake_ear: vosk, or auto without the
                        # model): the name from vosk's own words, the way the ears
                        # worked before the trained one arrived — not deaf, as before
                        tail, said = wake_at(words, spellings, 1)
                        if tail is not None:
                            woken = True
                            wake_t = time.monotonic()
                            _last_wake[0] = wake_t
                            heard_after = tail or ''
                            judge_note(f'WAKE vosk ({words!r})')
                            diag.event(arbitration='CANDIDATE', reason='vosk_name',
                                       vosk_text=words, wake_candidate=True,
                                       judge_called=False, final_wake=False,
                                       **diag_base('LISTENING', voiced))
                            lead = int(TUNE['wake_lead_ms'] * RATE * 2 / 1000.0)
                            utt = Utterance()
                            utt.lead = bytearray(ring[-lead:]) if lead else bytearray()
                            utt.lead_max = max(utt.LEAD, len(utt.lead))
                            det = Detector()
                            phrase_ms = 0.0
                    if woken:
                        # LISTENING: the phrase after the name gathers on the tape,
                        # its end comes from the same VAD endpointing as ever
                        if utt is not None:
                            utt.feed(clean, voiced)
                        if voiced:
                            phrase_ms += len(clean) / (RATE * 2.0) * 1000.0
                        if phrase_ms >= 400 and det.silence_ms >= VAD_TAIL:
                            break           # the voice died down: the phrase is over
                        if phrase_ms == 0 and time.monotonic() - wake_t > WAKE_AFTER:
                            # the name alone: her turn to greet (1.5 s of quiet)
                            return wake_result('', 'name_alone', wake_score)
                if not woken:
                    diag.event(arbitration='TIMEOUT',
                               reason='aborted' if self.abort else 'no_wake',
                               judge_called=False, final_wake=False,
                               **diag_base('SLEEP', voiced))
                    return {'woke': False, 'text': None}
                # the name was heard: the phrase on the tape goes to the configured
                # STT (Nemotron/Whisper read it, vosk stays for the phrase ends —
                # exactly as the settings say, раздел 7 ТЗ)
                said = utt.take() if utt is not None else b''
                text = ''
                if len(said) >= RATE:           # under a second is a breath, not a phrase
                    diag.event(arbitration='STT', reason='phrase_end', stt_start=True,
                               judge_called=False, final_wake=False,
                               **diag_base('LISTENING', voiced))
                    text = self.transcribe_said(said, lang, size, engine) or ''
                    diag.event(arbitration='STT', reason='stt_done', stt_text=text,
                               judge_called=False, final_wake=False,
                               **diag_base('LISTENING', voiced))
                if not text and heard_after:
                    text = heard_after          # the old ear's words past the name stand in
                return wake_result(text, 'phrase_after_name' if text else 'name_alone',
                                   wake_score)
            finally:
                if diag_wav and ear_tape:
                    wake_feed_wav(ear_tape)     # what the ear heard, for the shelf
                fed = ear_oww.get('fed', 0) if ear_oww else 0
                read = ear_oww.get('read', 0) if ear_oww else 0
                judge_note(f'SUMMARY fed={fed} read={read}')
                if ear_oww is not None:         # the little ear dies with the wake: a new
                    try:                        # one is born on it, or it haunts the RAM
                        ear_oww['proc'].stdin.close()
                        ear_oww['proc'].terminate()
                    except OSError:
                        pass
                if proc is not None:            # the recorder was born: it dies with the call
                    try:
                        proc.terminate()
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                    except OSError:
                        pass
                    finally:
                        try:
                            proc.stdout.close()
                        except OSError:
                            pass
                        try:
                            proc.stderr.close()
                        except OSError:
                            pass
                self.busy = False
                self.kind = None

    def stop(self):
        self.abort = True


class Tap:
    """the ears' diary: while it is on, its own thread keeps a recorder on the same
    microphone the listens and wakes use (PipeWire reads a source twice with no fuss),
    writes the sound into wav files in TAP_DIR (a fresh one every TAP_SEGMENT) and the
    heard phrases into TAP_DIR/recogn.txt as «time - phrase». It never touches the
    listens' lock: she answers the mic button while the tape goes on."""

    def __init__(self, ears):
        self.ears = ears
        self.lock = threading.Lock()
        self.on = False
        self.gen = 0                    # bumped on every change: the running loop gives up
        self.lines = 0                  # how many lines recogn.txt holds right now
        self.want = {'mic': None, 'lang': 'ru', 'size': 'small'}

    def set(self, on, mic=None, lang='ru', size='small', ear=None):
        """the app's wish: the diary on or off (a new mic or model restarts the tape)"""
        want = {'mic': mic, 'lang': lang,
                'size': size if size in ('small', 'big') else 'small',
                'ear': ear if ear in EAR_RANGE else 'mid'}
        with self.lock:
            if bool(on) and self.on and self.want == want:
                return self.status()    # already taping exactly this: nothing to restart
            self.want, self.on = want, bool(on)
            self.gen += 1               # whatever runs now winds down
            if self.on:
                threading.Thread(target=self._work, args=(self.gen,), daemon=True).start()
            save_tap_state(self.on, want)
        return self.status()

    def status(self):
        return {'on': self.on, 'dir': str(TAP_DIR), **(self.want if self.on else {})}

    def note(self, text):
        """one phrase into recogn.txt («time - phrase»), from the tap's own loop or
        from the wake's: while the wake holds the microphone the tap pauses its own
        recognizer, and the wake's finished phrases stand in for the diary's (one
        vosk stream instead of two on the same sound); only the freshest TAP_LINES
        lines stay, the old ones are rolled off"""
        if not text:
            return
        with self.lock:
            try:
                TAP_DIR.mkdir(parents=True, exist_ok=True)
                with open(TAP_DIR / 'recogn.txt', 'a', encoding='utf-8') as f:
                    f.write(tap_line(text) + '\n')
            except OSError:
                return
            self.lines += 1
            if self.lines > TAP_LINES:
                self.lines = tap_roll(TAP_DIR / 'recogn.txt', self.lines)

    def _work(self, mygen):
        """keep a recorder on tape; if it dies on its own, it goes again after a pause"""
        while self.on and self.gen == mygen:
            mic = self.want['mic']      # the diary hears the plain mic, not the clean end:
                                        # the canceller stays with one reader, and her own
            name, cmd = recorder_cmd(mic)   # voice stays on the tape (it is a diary)
            if not name:
                log('tap: no parecord, pw-record or arecord for the microphone')
                return
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except OSError as e:
                log(f'tap: the microphone would not start ({e}), again in 3 s')
                time.sleep(3)
                continue
            try:
                self._follow(proc, mygen, name)
            finally:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                except OSError:
                    pass
                finally:
                    for f in (proc.stdout, proc.stderr):
                        try:
                            f.close()
                        except OSError:
                            pass
            if self.on and self.gen == mygen:
                log(f'tap: {name} stopped on its own, again in 2 s')
                time.sleep(2)

    def _prune(self, keep=TAP_KEEP):
        """the diary is a ring: only the newest segments stay on the shelf"""
        try:
            for old in sorted(TAP_DIR.glob('ears-*.wav'))[:-keep]:
                old.unlink()
        except OSError:
            pass

    def _follow(self, proc, mygen, name):
        """the tape: the raw sound goes into the wav files, the cleaned sound into vosk"""
        from vosk import KaldiRecognizer
        import selectors
        model = self.ears.model(self.want['lang'], self.want['size'])   # a local ref: it
        TAP_DIR.mkdir(parents=True, exist_ok=True)                      # outlives the tap
        sel = selectors.DefaultSelector()
        sel.register(proc.stdout, selectors.EVENT_READ)
        rec = KaldiRecognizer(model, RATE)
        rec.SetWords(False)
        det = Detector()
        gmin, gk = ear_gate(self.want.get('ear'))
        st = {'hp': 0.0, 'floor': None, 'gate_min': gmin, 'gate_k': gk}    # the cleaner's memory
        seg = {'wav': None, 't0': 0.0, 'bytes': 0}
        self.lines = 0                     # how many lines recogn.txt holds right now
        try:
            with open(TAP_DIR / 'recogn.txt', encoding='utf-8') as f:
                self.lines = sum(1 for _ in f)
        except OSError:
            pass

        def say(text):
            """one phrase into recogn.txt, «time - phrase»"""
            self.note(text)

        def put(chunk):
            """the sound into the segment file: a fresh wav every TAP_SEGMENT seconds
            or TAP_MAX_BYTES, whichever comes first; old segments go off the shelf"""
            if (not seg['wav'] or seg['bytes'] >= TAP_MAX_BYTES
                    or time.monotonic() - seg['t0'] > TAP_SEGMENT):
                if seg['wav']:
                    seg['wav'].close()
                seg['wav'] = wave.open(str(TAP_DIR / time.strftime('ears-%Y%m%d-%H%M%S.wav')),
                                       'wb')
                seg['wav'].setnchannels(1)
                seg['wav'].setsampwidth(2)
                seg['wav'].setframerate(RATE)
                seg['t0'], seg['bytes'] = time.monotonic(), 0
                self._prune()
            seg['wav'].writeframes(chunk)
            seg['bytes'] += len(chunk)

        try:
            while self.on and self.gen == mygen:
                if not sel.select(1.0):
                    continue
                chunk = os.read(proc.stdout.fileno(), 4000)     # ~0.125 s of audio
                if not chunk:
                    die = proc.poll()
                    if die not in (None, 0):
                        log(f'tap: {name} died ({die})')
                    break
                put(chunk)                          # the diary keeps the untouched sound
                if self.ears.busy and self.ears.kind == 'wake':
                    continue        # the wake's own recognizer reads the same sound: its
                                    # finished phrases stand in for the diary's words, and
                                    # one vosk stream burns CPU instead of two
                chunk = hp_pass(chunk, st)          # hum and rumble go
                voiced = det.feed(chunk)            # the voice detector follows the frames
                chunk = gate_noise(chunk, st, voiced)    # the hiss dies, speech is kept
                if rec.AcceptWaveform(chunk):       # vosk heard the phrase end
                    say(json.loads(rec.FinalResult() or '{}').get('text', ''))
            say(json.loads(rec.FinalResult() or '{}').get('text', ''))
        finally:
            if seg['wav']:
                seg['wav'].close()


def download(zip_name, folder):
    """fetch and unpack a small model (~45 MB) from alphacephei.com, once per language"""
    HOME.mkdir(parents=True, exist_ok=True)
    url = f'https://alphacephei.com/vosk/models/{zip_name}.zip'
    log(f'downloading {zip_name} (~45 MB, once)...')
    tmp = HOME / (zip_name + '.part.zip')
    with urllib.request.urlopen(url, timeout=120) as r, open(tmp, 'wb') as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
    with zipfile.ZipFile(tmp) as z:
        z.extractall(HOME)
    tmp.unlink()
    if not (HOME / folder).exists():
        raise RuntimeError(f'{zip_name} did not unpack as expected')
    log(f'{zip_name} is in {HOME / folder}')


class Handler(BaseHTTPRequestHandler):
    ears = None

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == '/ping':
            return self._json({'ok': True, 'error': self.ears.error, 'version': VERSION,
                               'pid': os.getpid(),
                               'profile': ' '.join(sorted(MODELS)),
                               'recorder': recorder_cmd()[0],
                               'aec': bool(_echo['on']), 'tap': self.ears.tap.status(),
                               'mute': muted(), 'nemo': (NEMO_DIR / 'encoder.int8.onnx').exists(),
                               'downloading': self.ears.dl})
        if u.path == '/tap':
            # the ears' diary: /tap?on=1&lang=ru&size=small&mic=... starts it, on=0 stops,
            # a bare /tap just tells the state
            if q.get('on'):
                on = q['on'][0] not in ('', '0', 'false', 'no')
                mic = (q.get('mic') or [None])[0] or None
                lang = (q.get('lang') or ['ru'])[0]
                size = (q.get('size') or ['small'])[0]
                ear = (q.get('ear') or ['mid'])[0]
                try:
                    st = self.ears.tap.set(on, mic=mic, lang=lang, size=size, ear=ear)
                except Exception as e:      # noqa: BLE001 - any failure is reported over HTTP
                    self.ears.error = str(e)
                    return self._json({'error': str(e)}, 500)
                return self._json({'ok': True, **st})
            return self._json({'ok': True, **self.ears.tap.status()})
        if u.path == '/mics':
            # the microphones this recorder can hear, for Settings → Her ears → Microphone
            return self._json({'ok': True, 'recorder': recorder_cmd()[0], 'mics': list_mics()})
        if u.path == '/mute':
            # her ears close for a while (system.play_file asks, while her own voice
            # plays): nothing she says reaches vosk or the judge until it runs out
            try:
                secs = min(60.0, max(0.0, float((q.get('secs') or ['5'])[0])))
            except ValueError:
                secs = 5.0
            if secs:
                mute_for(secs)
            return self._json({'ok': True, 'mute': muted()})
        if u.path == '/stop':
            self.ears.stop()
            return self._json({'ok': True})
        engine = (q.get('engine') or ['whisper'])[0] or 'whisper'
        if u.path == '/wake':
            try:
                secs = min(300.0, max(5.0, float((q.get('secs') or ['20'])[0])))
            except ValueError:
                secs = 20.0
            lang = (q.get('lang') or ['ru'])[0]
            word = ' '.join((q.get('word') or ['юми'])[0].split()) or 'юми'
            size = (q.get('size') or ['small'])[0]
            mic = (q.get('mic') or [None])[0] or None
            ear = (q.get('ear') or ['mid'])[0]
            sensitivity = (q.get('sensitivity') or ['normal'])[0]
            confirm = q.get('confirm') and q['confirm'][0] not in ('', '0', 'false', 'no')
            model_size = (q.get('model_size') or ['small'])[0]
            judge_log = q.get('judge_log') and q['judge_log'][0] not in ('', '0', 'false', 'no')
            diag = q.get('diag') and q['diag'][0] not in ('', '0', 'false', 'no')
            oww_ear = (q.get('oww_ear') or ['auto'])[0]
            try:
                res = self.ears.wake(lang, word, secs, size=size, mic=mic, ear=ear,
                                     sensitivity=sensitivity, confirm=confirm,
                                     model_size=model_size, judge_log=judge_log,
                                     engine=engine, oww_ear=oww_ear, diag=diag)
            except Exception as e:          # noqa: BLE001 - any failure is reported over HTTP
                self.ears.error = str(e)
                return self._json({'error': str(e)}, 500)
            return self._json({'ok': True, 'woke': res['woke'], 'text': res.get('text'),
                               'stop': bool(res.get('stop'))})
        if u.path != '/listen':
            return self._json({'error': 'no such path'}, 404)
        if self.ears.busy and self.ears.kind == 'wake':
            # the mic button was clicked while she was waiting for her name:
            # the wake-word wait gives the microphone up to this listen
            self.ears.abort = True
        try:
            secs = min(30.0, max(1.0, float((q.get('secs') or ['7'])[0])))
        except ValueError:
            secs = 7.0
        lang = (q.get('lang') or ['ru'])[0]
        size = (q.get('size') or ['small'])[0]
        mic = (q.get('mic') or [None])[0] or None
        ear = (q.get('ear') or ['mid'])[0]
        try:
            res = self.ears.listen(lang, secs, quiet=min(6.0, secs), size=size, mic=mic,
                                   ear=ear, engine=engine)
        except Exception as e:          # noqa: BLE001 - any failure is reported over HTTP
            self.ears.error = str(e)
            return self._json({'error': str(e)}, 500)
        self._json({'ok': True, 'text': res['text'], 'vosk': res.get('vosk', '')})

    def log_message(self, *_a):         # the log stays for real problems
        pass


def read_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return None


def write_state(port, pid=None):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    # pid of the daemon that serves this port, not of whoever happens to write the file:
    # a false pid here makes the stale twin unkillable (ears.py would shoot a corpse)
    STATE.write_text(json.dumps({'port': port, 'pid': pid if pid else os.getpid(),
                                 'version': VERSION}))


def tap_line(text, now=None):
    """one line of recogn.txt: «time - phrase»"""
    stamp = time.strftime('%H:%M:%S', time.localtime(now if now is not None else time.time()))
    return f'{stamp} - {text}'


def tap_roll(path, lines):
    """recogn.txt is rewritten without its oldest lines, the freshest TAP_LINES stay;
    the line count after the roll comes back (the old one when the file would not move)"""
    try:
        with open(path, encoding='utf-8') as f:
            keep = f.readlines()[-TAP_LINES:]
        with open(path, 'w', encoding='utf-8') as f:
            f.writelines(keep)
        return len(keep)
    except OSError:
        return lines


def save_tap_state(on, want=None):
    """the diary's wish across the daemon's restarts: it resumes tapping by itself"""
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        (STATE.parent / 'tap.json').write_text(
            json.dumps({'on': bool(on), 'want': want or {}}, ensure_ascii=False))
    except OSError:
        pass


def load_tap_state():
    """what the diary was asked last, or None when it was off (or was never asked)"""
    try:
        d = json.loads((STATE.parent / 'tap.json').read_text())
    except (OSError, ValueError):
        return None
    if not (isinstance(d, dict) and d.get('on')):
        return None
    w = d.get('want') or {}
    return {'mic': w.get('mic'), 'lang': w.get('lang') or 'ru',
            'size': w.get('size') if w.get('size') in ('small', 'big') else 'small',
            'ear': w.get('ear') if w.get('ear') in EAR_RANGE else 'mid'}


def ping_info(port, timeout=0.5):
    """the twin's /ping dict, or None when nobody answers there"""
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/ping', timeout=timeout) as r:
            d = json.loads(r.read().decode())
            return d if d.get('ok') else None
    except (OSError, ValueError):
        return None


def try_forget(port):
    """remove the port file only if it is still ours"""
    try:
        info = read_state()
        if info and info.get('port') == port:
            STATE.unlink()
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser(description="Yumi's ears: a local Vosk over the microphone, HTTP")
    ap.add_argument('--port', type=int, default=8766)
    args = ap.parse_args()

    try:
        import vosk                     # noqa: F401 - the reason tools/stt_setup.sh exists
    except ImportError:
        sys.stderr.write('yumi-stt: no vosk here (run tools/stt_setup.sh)\n')
        sys.exit(1)

    Handler.ears = Ears()
    resumed = load_tap_state()
    if resumed:                         # the diary keeps taping across the daemon's restarts
        Handler.ears.tap.set(True, **resumed)
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    except OSError:                     # the port is taken: a twin may already be there
        for _ in range(15):             # 3 s: the winner writes its state at once, and a stale
            info = read_state()         # version (one ears.py is about to kill) frees the port
            twin = ping_info(info.get('port')) if info else None
            if twin and twin.get('version') == VERSION:
                # the twin on the port stays the daemon: the state file keeps ITS pid,
                # not ours — a false pid would make it unkillable when the code moves on
                write_state(info['port'], twin.get('pid') or info.get('pid'))
                log(f'already running on port {info["port"]}')
                return
            try:
                server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
                break
            except OSError:
                time.sleep(0.2)
        else:
            # nobody answers and the port does not free: it belongs to someone else.
            # A second daemon on a random port would be an orphan nobody ever shuts
            # down (gigabytes of models burning forever), so there is no second daemon.
            log(f'port {args.port} is held and no twin of mine answers there — I quit')
            sys.exit(1)
    port = server.server_address[1]
    write_state(port)
    atexit.register(try_forget, port)
    signal.signal(signal.SIGTERM, goodbye)   # a SIGTERM leaves through atexit: the port file is forgotten
    log(f'listening on 127.0.0.1:{port}')
    server.serve_forever()


def goodbye(*_):
    raise SystemExit(0)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass

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
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import wave
import zipfile
from array import array
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
VERSION = 13                            # bumped when the protocol changes: stale twins are restarted
WAKE_AFTER = 0.7                        # s of quiet after the name before she asks what you wanted
WAKE_TAPE = RATE * 8 * 2                # bytes of the recent sound the judge reads: ~8 s
WAKE_REJUDGE = 3.0                      # s before the judge reads the tape again after a dream
VAD_TAIL = 700                          # ms of quiet after speech: the phrase is really over
WAKE_SENSITIVITY = {'normal': 1, 'strict': 0}  # edit-distance budget for a wake match
TAP_DIR = Path(os.environ.get('YUMI_TAP_DIR') or '/tmp/yumi')   # the ears' diary lives here
TAP_SEGMENT = 600.0                     # s of tape in one wav, then the next file starts
TAP_MAX_BYTES = 10 * 1024 * 1024        # a segment is no bigger than this: the diary is small
TAP_KEEP = 5                            # only the newest segments stay: the diary is a ring
TAP_LINES = 1000                        # recogn.txt holds the freshest this many lines

ECHO_SOURCE = 'yumi_ec'                 # the echo-cancel source the ears record through
ECHO_SINK = 'yumi_ec_sink'              # where her own sounds go, so they can be subtracted
_echo = {'on': False, 'master': None, 'module': None, 'checked': 0.0, 'failed': 0}
_echo_gate = threading.Lock()   # echo_ready runs from several threads: one at a time
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
        if _echo['on'] and _echo_want(mic) != _echo['master']:
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
    print(f'[yumi-stt] {msg}', flush=True)


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
                   'еми', 'еле', 'уме', 'умэ', 'джуми', 'yumi', 'umi', 'yume', 'emi'],
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


def near(a, b):
    """How many letters separate the two words (the hearsay of Vosk): one letter forgiven
    to short words, two to the long ones where one slips in anyway. Returns the edit
    distance, or lim+1 when it is already over the budget."""
    if a == b:
        return 0
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

    def feed(self, chunk, voiced):
        """one cleaned chunk of s16 mono; True while a voice has ever passed by"""
        if voiced or self.buf:
            self.buf += chunk       # once the voice began, the inner quiet is a phrase's own
        else:
            self.lead += chunk
            if len(self.lead) > self.LEAD:
                del self.lead[:-self.LEAD]
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
    budget is how many letters may differ from any known spelling (0 = exact only)."""
    toks = [t.strip('.,!?"«»…:;()[]-—').lower().replace('ё', 'е')
            for t in text.split()]
    toks = [t for t in toks if t]
    for n in (3, 2, 1):                     # the fullest hear-say first, near the start:
        for i in range(0, min(len(toks), 8) - n + 1):   # a name is said first, not deep
            said = ''.join(toks[i:i + n])                # glued hear-say like «ю и ми»
            dist = min(near(said, s) for s in spellings)
            if dist > budget:
                continue
            # a common word one letter from the name's hear-say («иди», «или», «уже»)
            # must not wake her: it counts as the name only when a true spelling of the
            # name fits the sound no worse than the stop word does
            if any(near(said, w) < dist for w in WAKE_STOPS):
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


WAKE_STOPS = {'иди', 'или', 'уже', 'ели', 'юли', 'умер'}    # the ears' false names: never wake on these
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


_judge = threading.Lock()   # one whisper reading at a time, wake to wake


def transcribe_model(model, pcm, lang, hotwords=None):
    """s16 mono through a whisper that is already in hand ('' for silence): a function
    of its own so a thread may read with a model the lock will not hand out again
    (an RLock is open to its own thread only). hotwords leans the reading toward
    the wake word: the small whisper keeps dropping a short name said into the
    room — «юми» comes back as «умень» or not at all"""
    import numpy as np
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    try:
        segments, _info = model.transcribe(
            audio, language=lang or None, beam_size=1, vad_filter=True,
            condition_on_previous_text=False, hotwords=hotwords)
    except TypeError:           # an older faster-whisper knows no hotwords
        segments, _info = model.transcribe(
            audio, language=lang or None, beam_size=1, vad_filter=True,
            condition_on_previous_text=False)
    return ' '.join(s.text for s in segments).strip()


class Ears:
    """downloads the models one by one and answers one listen at a time"""

    def __init__(self):
        self.models = {}
        self.wsp = {}                   # faster-whisper models, one per (lang, size)
        self.error = None
        self.busy = False
        self.abort = False
        self.kind = None                # what the lock holds now: 'listen' or 'wake'
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
                download(zip_name, folder)
            m = Model(str(path))
            for other in [k for k in self.models if k[0] == lang and k != key]:
                del self.models[other]      # a big model is many: one per language at a time
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
            m = WhisperModel(name, device='cpu', compute_type='int8',
                             download_root=str(path))
            for other in [k for k in self.wsp if k[0] == lang and k != key]:
                del self.wsp[other]
            self.wsp[key] = m
            log(f'whisper: {name} is ready')
            return m

    def transcribe(self, pcm, lang, size='small'):
        """bytes of s16 mono RATE through faster-whisper: the words it hears ('' for
        silence). vad_filter keeps the quiet out (whisper dreams on silence), and the
        chain is not carried on (so one dream does not write the next)"""
        return transcribe_model(self.whisper(lang, size), pcm, lang)

    def listen(self, lang, secs, quiet, size='small', mic=None, ear=None):
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
                    chunk = hp_pass(chunk, st)            # hum and rumble go
                    voiced = det.feed(chunk)              # the voice detector follows the frames
                    chunk = gate_noise(chunk, st, voiced) # the hiss dies, speech is never touched
                    utt.feed(chunk, voiced)               # the phrase collects on the tape
                    if rec.AcceptWaveform(chunk):         # vosk heard the phrase end
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
                if len(said) >= RATE:         # under a second is a breath, not a phrase
                    try:
                        text = self.transcribe(said, lang, size)   # whisper reads the tape
                    except Exception as e:    # noqa: BLE001 - whisper is best effort
                        log('whisper failed, vosk answers instead: ' + str(e)[-120:])
                if not text:                  # whisper silent or away: vosk's words stand in
                    text = ' '.join(json.loads(rec.FinalResult() or '{}').get('text', '').split())
                return text
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
             sensitivity='normal', confirm=True, model_size=None, judge_log=False):
        """wait for the wake word, keeping the microphone on:
        {'woke': True, 'text': the phrase after it} — the name was heard, the rest came
        {'woke': True, 'text': ''} — the name alone, a phrase may still come
        {'woke': False, ...} — quiet the whole 'secs', or called off by /stop, or busy.
        'text' is what was said after the wake word: the app recognizes nothing twice.
        The name is not taken at vosk's word alone: when his letters seem to spell it,
        the tape of the last seconds goes to whisper for a yes or a no (vosk dreams of
        the name in every video); with whisper away, his word is taken as it comes."""
        from vosk import KaldiRecognizer
        import selectors
        echo = echo_ready(mic)
        if echo and not (shutil.which('pw-record') or shutil.which('parecord')):
            echo = None                 # arecord cannot aim at a PipeWire source: plain mic then
        name, cmd = recorder_cmd(echo or mic)
        if not name:
            raise RuntimeError('no parecord, pw-record or arecord for the microphone')
        model = self.model(lang, model_size or size)
        word = ' '.join(str(word).split()).lower() or 'юми'
        budget = WAKE_SENSITIVITY.get(sensitivity, WAKE_SENSITIVITY['normal'])
        spellings = variants(word) | hearsay_load(word)
        # whisper is the judge: loaded here, while the lock is ours, because the judge
        # thread may not ask for it — an RLock is open to its own thread only
        try:
            wmodel = self.whisper(lang, size)
        except Exception as e:      # noqa: BLE001 - whisper is best effort, vosk stands
            wmodel = None
            log('whisper will not judge, vosk answers alone: ' + str(e)[-120:])
        judge_lock = threading.Lock()

        def judge_note(what):
            """one line into the wake's diary (judge.log): what vosk heard, what went
            to the judge and what came back — without it a failed wake is a guess"""
            if not judge_log:
                return
            TAP_DIR.mkdir(parents=True, exist_ok=True)
            try:
                with open(TAP_DIR / 'judge.log', 'a', encoding='utf-8') as f:
                    f.write(f'{time.strftime("%H:%M:%S")} {what}\n')
            except OSError:
                pass

        def judge(says):
            """whisper reads the tape in its own thread (the words come in while the
            judge still reads) and leaves its answer for the wake loop to find"""
            with _judge:                # wake to wake: one whisper reading at a time
                try:
                    text = transcribe_model(wmodel, says, lang, hotwords=word)
                except Exception as e:  # noqa: BLE001 - a failed verdict is no verdict
                    log('the judge failed, vosk answers alone: ' + str(e)[-120:])
                    text = ''
                judge_note(f'JUDGE {text!r}')
                after, said = wake_at(text, spellings)
                with judge_lock:
                    verdict.update(busy=False, ok=after is not None, after=after or '',
                                   said=said, t=time.monotonic())
        with self.lock:                     # one microphone at a time
            if self.busy:
                return {'woke': False, 'text': None}
            self.busy, self.abort, self.kind = True, False, 'wake'
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
                gmin, gk = ear_gate(ear)
                st = {'hp': 0.0, 'floor': None, 'gate_min': gmin, 'gate_k': gk}     # the cleaner's memory
                t0 = time.monotonic()
                heard = False               # at least one byte of sound has arrived
                woken = False               # the wake word has passed by already
                since = t0                  # when the heard words last changed
                last = ''                   # the previous half-heard words, to notice staleness
                logged = ''                 # the words the wake's diary last wrote, one line each
                tape = bytearray()          # the judge's tape: the newest clean sound stays
                verdict = {'busy': False, 'ok': None, 'after': '', 'said': None,
                           't': 0.0}        # whisper's fresh answer lives here
                heard_after = ''            # the words whisper heard past the name
                while time.monotonic() - t0 < secs:
                    if self.abort:
                        break               # /stop, or a listen that needs the microphone
                    if not sel.select(0.5):
                        if not heard and time.monotonic() - t0 > 8:
                            raise RuntimeError(f'{name} gave no sound')
                        if woken and time.monotonic() - since > WAKE_AFTER:
                            # the name passed by, nothing followed it: her turn to greet
                            return {'woke': True, 'text': heard_after}
                        continue
                    chunk = os.read(proc.stdout.fileno(), 4000)     # ~0.125 s of audio
                    if not chunk:
                        die, err = proc.poll(), ''
                        if die:
                            try:
                                err = ' ' + proc.stderr.read().decode(errors='replace')[-200:]
                            except OSError:
                                pass
                        if die not in (None, 0):
                            raise RuntimeError(f'{name} died ({die}){err}'.rstrip())
                        if woken:
                            return {'woke': True, 'text': heard_after}   # the name was on tape
                        break
                    heard = True
                    chunk = hp_pass(chunk, st)            # hum and rumble go
                    voiced = det.feed(chunk)              # the voice detector follows the frames
                    chunk = gate_noise(chunk, st, voiced) # the hiss dies, speech is never touched
                    tape += chunk                         # the judge reads the same clean sound
                    if len(tape) > WAKE_TAPE:
                        del tape[:-WAKE_TAPE]
                    ended = rec.AcceptWaveform(chunk)   # True: an utterance ended just now
                    if ended:
                        words = json.loads(rec.FinalResult() or '{}').get('text', '')
                    else:
                        words = json.loads(rec.PartialResult() or '{}').get('partial', '')
                    if woken and words != last:
                        since = time.monotonic()    # the half-heard moves on: the wait restarts
                    last = words
                    if stop_at(words):
                        # «стоп» overrides everything: she stops whatever she is busy
                        # with, even her own voice mid-word, wake word or not
                        return {'woke': False, 'stop': True, 'text': words}
                    if woken and det.speech_ms and det.silence_ms >= VAD_TAIL:
                        # the voice died down after the name: whatever came after is the phrase
                        hit, _src = wake_at(words, spellings, budget)
                        said = hit if hit is not None else words
                        said = ' '.join(said.split())
                        if not said and heard_after:
                            said = heard_after      # vosk went quiet: whisper's words stand in
                        if said:
                            return {'woke': True, 'text': said}
                        return {'woke': True, 'text': ''}   # her turn to greet
                    if woken and time.monotonic() - since > WAKE_AFTER:
                        # the name passed by and quiet followed: what the judge heard
                        # past the name stands, even when vosk's own words went quiet
                        return {'woke': True, 'text': heard_after}
                    # the judge's answer, when it has come: a yes wakes her, a no means
                    # the letters were a dream of vosk's (a name in a video's stream);
                    # it is read even when vosk went quiet — a name said into silence
                    # must not hang unanswered
                    if not woken and wmodel is not None and not verdict['busy'] \
                            and verdict['ok'] is not None:
                        with judge_lock:
                            ok, after, said = verdict['ok'], verdict['after'], verdict['said']
                            verdict['ok'] = None        # the verdict is spent
                        if ok:
                            woken = True                # whisper heard the name too
                            since = time.monotonic()
                            heard_after = after
                            if said and said not in spellings:
                                hearsay_save(word, said)    # a hear-say the judge approved
                    if not words.strip():
                        continue
                    if words != logged:         # every fresh half-heard once, not each chunk
                        logged = words
                        judge_note(f'VOSK {words!r}')
                    hit, src = wake_at(words, spellings)
                    if not woken and hit is not None and wmodel is None:
                        # no judge today: vosk's word is taken as it comes
                        woken = True                # she hears her name: keep on recording
                        since = time.monotonic()
                        if src and src not in spellings:
                            hearsay_save(word, src)     # a hear-say she never met: learn it
                    elif not woken and hit is not None:
                        if name_exact(words, spellings):
                            # the name spelled out whole and right: vosk is sure,
                            # the judge would only add seconds of waiting
                            woken = True
                            since = time.monotonic()
                        elif not verdict['busy'] and verdict['ok'] is None \
                                and time.monotonic() - verdict['t'] > WAKE_REJUDGE:
                            # a near-miss spelling: the tape goes to the judge, a fresh
                            # reading only when the last one is answered and the wait
                            # between reads has passed
                            verdict['busy'] = True
                            judge_note(f'SENT {src!r}')
                            threading.Thread(target=judge, args=(bytes(tape),),
                                             daemon=True).start()
                    if not woken or not ended:
                        continue    # not her name yet, or the phrase is still forming
                    said = hit if hit is not None else words
                    # (the name passed by in an earlier phrase: the whole utterance is the tail)
                    said = ' '.join(said.split())
                    if not said and heard_after:
                        said = heard_after          # vosk went quiet: whisper's words stand in
                    if said:
                        return {'woke': True, 'text': said}
                    since = time.monotonic()        # the name alone: a pause for what follows
                if woken:
                    return {'woke': True, 'text': heard_after}   # the name was heard
                return {'woke': False, 'text': None}
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
        lines = 0                          # how many lines recogn.txt holds right now
        try:
            with open(TAP_DIR / 'recogn.txt', encoding='utf-8') as f:
                lines = sum(1 for _ in f)
        except OSError:
            pass

        def say(text):
            """one phrase into recogn.txt, «time - phrase»; only the freshest
            TAP_LINES lines stay: once over, the file is rewritten without the old"""
            nonlocal lines
            if not text:
                return
            try:
                with open(TAP_DIR / 'recogn.txt', 'a', encoding='utf-8') as f:
                    f.write(tap_line(text) + '\n')
                lines += 1
                if lines > TAP_LINES:
                    lines = tap_roll(TAP_DIR / 'recogn.txt', lines)
            except OSError:
                pass

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
                               'profile': ' '.join(sorted(MODELS)),
                               'recorder': recorder_cmd()[0],
                               'aec': bool(_echo['on']), 'tap': self.ears.tap.status()})
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
        if u.path == '/stop':
            self.ears.stop()
            return self._json({'ok': True})
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
            try:
                res = self.ears.wake(lang, word, secs, size=size, mic=mic, ear=ear,
                                     sensitivity=sensitivity, confirm=confirm,
                                     model_size=model_size, judge_log=judge_log)
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
            text = self.ears.listen(lang, secs, quiet=min(6.0, secs), size=size, mic=mic,
                                    ear=ear)
        except Exception as e:          # noqa: BLE001 - any failure is reported over HTTP
            self.ears.error = str(e)
            return self._json({'error': str(e)}, 500)
        self._json({'ok': True, 'text': text})

    def log_message(self, *_a):         # the log stays for real problems
        pass


def read_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return None


def write_state(port):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({'port': port, 'pid': os.getpid(), 'version': VERSION}))


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


def ping(port, timeout=0.5):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/ping', timeout=timeout) as r:
            return json.loads(r.read().decode()).get('ok')
    except (OSError, ValueError):
        return False


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
    except OSError:                     # the port is taken: an older twin may already be there
        for _ in range(15):             # 3 s: the winner writes its state at once, and a stale
            info = read_state()         # version (one ears.py is about to kill) frees the port
            if info and ping(info['port']) and info.get('version') == VERSION:
                write_state(info['port'])
                log(f'already running on port {info["port"]}')
                return
            try:
                server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
                break
            except OSError:
                time.sleep(0.2)
        else:
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    port = server.server_address[1]
    write_state(port)
    atexit.register(try_forget, port)
    log(f'listening on 127.0.0.1:{port}')
    server.serve_forever()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass

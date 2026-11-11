#!/usr/bin/env python3
"""Her ears: a local Vosk over the microphone as a tiny HTTP server (no GTK, no Yumi imports).

Yumi's app side (yumi/ears.py) starts this with a Python that has vosk (see
tools/stt_setup.sh) and asks it to listen: /listen?secs=8&lang=ru. The small models
(~45 MB each, Russian and English) are downloaded on the first use into
~/.local/share/yumi/stt; after that a phrase takes a moment.

The microphone is read with whatever the desktop has: parecord, pw-record or arecord.
A particular microphone can be named (mic=... from the settings; /mics lists the ones
the current recorder can hear).
A click on her mic button starts a listen; vosk hears the end of the phrase, or
secs pass, or another click asks /stop. /wake is the same microphone held on longer:
vosk waits for the wake word (юми), and the moment it passes by the app gets the
phrase that follows — nothing is recognized twice.

Before vosk the sound is cleaned here: a one-pole high-pass (hum and rumble go),
a noise gate with a floor that follows the quiet (steady hiss and fan die), and a
webrtc voice detector (webrtcvad, best effort) that finds where speech really ends,
so vosk does not guess the end from its own timing.

On top of the microphone there is a webrtc echo-cancel source (module-echo-cancel
over pactl, best effort): the chosen mic feeds it, the ears record from the clean
end, and her own sounds played into the echo-cancel sink are subtracted — she never
hears herself. When it cannot be set up, the ears fall back to the plain mic.

Two sizes per language: the small one (~45 MB) is quick and always on, the big one
(~1.8 GB here and here) hears at least twice as well; the app asks for it with
size=big in the settings (Hearing accuracy / her Hearing accuracy).
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
VERSION = 8                             # bumped when the protocol changes: stale twins are restarted
WAKE_AFTER = 0.7                        # s of quiet after the name before she asks what you wanted
VAD_TAIL = 700                          # ms of quiet after speech: the phrase is really over
TAP_DIR = Path(os.environ.get('YUMI_TAP_DIR') or '/tmp/yumi')   # the ears' diary lives here
TAP_SEGMENT = 600.0                     # s of tape in one wav, then the next file starts
TAP_MAX_BYTES = 10 * 1024 * 1024        # a segment is no bigger than this: the diary is small
TAP_KEEP = 5                            # only the newest segments stay: the diary is a ring
TAP_LINES = 1000                        # recogn.txt holds the freshest this many lines

ECHO_SOURCE = 'yumi_ec'                 # the echo-cancel source the ears record through
ECHO_SINK = 'yumi_ec_sink'              # where her own sounds go, so they can be subtracted
_echo = {'on': False, 'master': None, 'module': None, 'checked': 0.0, 'failed': 0}
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


def _echo_load(master):
    """pactl loads the webrtc echo-cancel around the mic; True when the clean end appeared"""
    argv = ['pactl', 'load-module', 'module-echo-cancel',
            f'source_name={ECHO_SOURCE}', f'sink_name={ECHO_SINK}', 'aec_method=webrtc']
    if master:
        argv.append(f'source_master={master}')
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    if r.returncode == 0 and ECHO_SOURCE in _pactl_short('sources'):
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
    now = time.monotonic()
    if now - _echo['checked'] >= 20:
        _echo['checked'] = now
        if ECHO_SOURCE in _pactl_short('sources'):
            _echo['on'] = True
            for line in _pactl_short('modules').splitlines():
                p = line.split('\t')
                if len(p) >= 3 and 'module-echo-cancel' in p[1] and ECHO_SOURCE in p[2]:
                    try:
                        _echo['module'] = int(p[0])
                    except ValueError:
                        pass
                    for arg in p[2].split():     # what the running canceller listens to
                        if arg.startswith('source_master='):
                            _echo['master'] = arg.split('=', 1)[1] or None
                    break
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
    """True when the two words differ by at most a couple of letters (the hearsay of
    Vosk): one letter forgiven to short words, two to the long ones where one slips in anyway"""
    if a == b:
        return True
    lim = 2 if min(len(a), len(b)) >= 6 else 1
    if abs(len(a) - len(b)) > lim:
        return False
    # a plain edit distance, cut short: a substitution, insertion or deletion is fine
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
            if min(cur) > lim and min(prev) > lim:
                return False
        prev = cur
    return prev[-1] <= lim


def wake_at(text, spellings):
    """what came after the wake word and the spelling the ears wrote it with, or (None, None);
    whole words only, anywhere in the first words of the phrase («юми» or behind a filler
    «а можно ми»), glued hear-say like «ю и ми» counts too: «юмизнает» must not wake her"""
    toks = [t.strip('.,!?"«»…:;()[]-—').lower().replace('ё', 'е')
            for t in text.split()]
    toks = [t for t in toks if t]
    for n in (3, 2, 1):                     # the fullest hear-say first, anywhere she turns up
        for i in range(len(toks) - n + 1):
            said = ''.join(toks[i:i + n])
            if any(near(said, s) for s in spellings):
                return ' '.join(toks[i + n:]), said
    return None, None


class Ears:
    """downloads the models one by one and answers one listen at a time"""

    def __init__(self):
        self.models = {}
        self.error = None
        self.busy = False
        self.abort = False
        self.kind = None                # what the lock holds now: 'listen' or 'wake'
        self.lock = threading.Lock()
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
        model = self.model(lang, size)
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
                    if rec.AcceptWaveform(chunk):         # vosk heard the phrase end
                        break
                    if det.speech_ms and det.silence_ms >= VAD_TAIL:
                        break                             # the voice died down: the phrase is over
                    if time.monotonic() - t0 > quiet and not json.loads(
                            rec.PartialResult() or '{}').get('partial'):
                        break                             # nothing but quiet: do not wait
                return ' '.join(json.loads(rec.FinalResult() or '{}').get('text', '').split())
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

    def wake(self, lang, word, secs, size='small', mic=None, ear=None):
        """wait for the wake word, keeping the microphone on:
        {'woke': True, 'text': the phrase after it} — the name was heard, the rest came
        {'woke': True, 'text': ''} — the name alone, a phrase may still come
        {'woke': False, ...} — quiet the whole 'secs', or called off by /stop, or busy.
        'text' is what was said after the wake word: the app recognizes nothing twice."""
        from vosk import KaldiRecognizer
        import selectors
        echo = echo_ready(mic)
        if echo and not (shutil.which('pw-record') or shutil.which('parecord')):
            echo = None                 # arecord cannot aim at a PipeWire source: plain mic then
        name, cmd = recorder_cmd(echo or mic)
        if not name:
            raise RuntimeError('no parecord, pw-record or arecord for the microphone')
        model = self.model(lang, size)
        word = ' '.join(str(word).split()).lower() or 'юми'
        spellings = variants(word) | hearsay_load(word)
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
                while time.monotonic() - t0 < secs:
                    if self.abort:
                        break               # /stop, or a listen that needs the microphone
                    if not sel.select(0.5):
                        if not heard and time.monotonic() - t0 > 8:
                            raise RuntimeError(f'{name} gave no sound')
                        if woken and time.monotonic() - since > WAKE_AFTER:
                            # the name passed by, nothing followed it: her turn to greet
                            return {'woke': True, 'text': ''}
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
                            return {'woke': True, 'text': ''}   # the name was on tape
                        break
                    heard = True
                    chunk = hp_pass(chunk, st)            # hum and rumble go
                    voiced = det.feed(chunk)              # the voice detector follows the frames
                    chunk = gate_noise(chunk, st, voiced) # the hiss dies, speech is never touched
                    ended = rec.AcceptWaveform(chunk)   # True: an utterance ended just now
                    if ended:
                        words = json.loads(rec.FinalResult() or '{}').get('text', '')
                    else:
                        words = json.loads(rec.PartialResult() or '{}').get('partial', '')
                    if woken and words != last:
                        since = time.monotonic()    # the half-heard moves on: the wait restarts
                    last = words
                    if woken and det.speech_ms and det.silence_ms >= VAD_TAIL:
                        # the voice died down after the name: whatever came after is the phrase
                        hit, _src = wake_at(words, spellings)
                        said = hit if hit is not None else words
                        said = ' '.join(said.split())
                        if said:
                            return {'woke': True, 'text': said}
                        return {'woke': True, 'text': ''}   # her turn to greet
                    if woken and time.monotonic() - since > WAKE_AFTER:
                        # the name passed by and quiet followed: her turn to greet
                        return {'woke': True, 'text': ''}
                    if not words.strip():
                        continue
                    hit, src = wake_at(words, spellings)
                    if not woken and hit is not None:
                        woken = True                # she hears her name: keep on recording
                        since = time.monotonic()
                        if src and src not in spellings:
                            hearsay_save(word, src)     # a hear-say she never met: learn it
                    if not woken or not ended:
                        continue    # not her name yet, or the phrase is still forming
                    said = hit if hit is not None else words
                    # (the name passed by in an earlier phrase: the whole utterance is the tail)
                    said = ' '.join(said.split())
                    if said:
                        return {'woke': True, 'text': said}
                    since = time.monotonic()        # the name alone: a pause for what follows
                if woken:
                    return {'woke': True, 'text': ''}   # the name was heard, the sound went on
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
            mic = self.want['mic']
            echo = echo_ready(mic)
            if echo and not (shutil.which('pw-record') or shutil.which('parecord')):
                echo = None             # arecord cannot aim at a PipeWire source: plain mic
            name, cmd = recorder_cmd(echo or mic)
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
                secs = min(60.0, max(5.0, float((q.get('secs') or ['20'])[0])))
            except ValueError:
                secs = 20.0
            lang = (q.get('lang') or ['ru'])[0]
            word = ' '.join((q.get('word') or ['юми'])[0].split()) or 'юми'
            size = (q.get('size') or ['small'])[0]
            mic = (q.get('mic') or [None])[0] or None
            ear = (q.get('ear') or ['mid'])[0]
            try:
                res = self.ears.wake(lang, word, secs, size=size, mic=mic, ear=ear)
            except Exception as e:          # noqa: BLE001 - any failure is reported over HTTP
                self.ears.error = str(e)
                return self._json({'error': str(e)}, 500)
            return self._json({'ok': True, 'woke': res['woke'], 'text': res.get('text')})
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
        info = read_state()
        if info and ping(info['port']):
            write_state(info['port'])
            log(f'already running on port {info["port"]}')
            return
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

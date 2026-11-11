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

Two sizes per language: the small one (~45 MB) is quick and always on, the big one
(~1.8 GB here and there) hears at least twice as well; the app asks for it with
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
import zipfile
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
VERSION = 5                             # bumped when the protocol changes: stale twins are restarted
WAKE_AFTER = 0.7                        # s of quiet after the name before she asks what you wanted
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
    """every microphone the current recorder can hear: [{'name': ..., 'label': ...}], [] when none"""
    name, _cmd = recorder_cmd()
    try:
        if name == 'pw-record':
            return _pw_mics() if shutil.which('pw-cli') else _pactl_mics()
        if name == 'parecord':
            return _pactl_mics()
        if name == 'arecord':
            return _arecord_mics()
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    return []


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
    """what came after the wake word in the phrase, or None; only whole words count
    and only near the start («юми» or behind a filler «ну, юми»; glued hear-say like
    «ю и ми» counts too): «юмизнает» must not wake her"""
    toks = [t.strip('.,!?"«»…:;()[]-—').lower().replace('ё', 'е')
            for t in text.split()]
    toks = [t for t in toks if t]
    for i, n in ((0, 3), (0, 2), (0, 1), (1, 2), (1, 1)):   # the fullest hear-say first
        if i + n <= len(toks) and any(near(''.join(toks[i:i + n]), s) for s in spellings):
            return ' '.join(toks[i + n:])
    return None


class Ears:
    """downloads the models one by one and answers one listen at a time"""

    def __init__(self):
        self.models = {}
        self.error = None
        self.busy = False
        self.abort = False
        self.kind = None                # what the lock holds now: 'listen' or 'wake'
        self.lock = threading.Lock()

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

    def listen(self, lang, secs, quiet, size='small', mic=None):
        """hear one phrase: the recognized text ('' when there was nothing)"""
        from vosk import KaldiRecognizer
        import selectors
        name, cmd = recorder_cmd(mic)
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
                    if rec.AcceptWaveform(chunk):         # vosk heard the phrase end
                        break
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

    def wake(self, lang, word, secs, size='small', mic=None):
        """wait for the wake word, keeping the microphone on:
        {'woke': True, 'text': the phrase after it} — the name was heard, the rest came
        {'woke': True, 'text': ''} — the name alone, a phrase may still come
        {'woke': False, ...} — quiet the whole 'secs', or called off by /stop, or busy.
        'text' is what was said after the wake word: the app recognizes nothing twice."""
        from vosk import KaldiRecognizer
        import selectors
        name, cmd = recorder_cmd(mic)
        if not name:
            raise RuntimeError('no parecord, pw-record or arecord for the microphone')
        model = self.model(lang, size)
        spellings = variants(' '.join(str(word).split()).lower() or 'юми')
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
                    ended = rec.AcceptWaveform(chunk)   # True: an utterance ended just now
                    if ended:
                        words = json.loads(rec.FinalResult() or '{}').get('text', '')
                    else:
                        words = json.loads(rec.PartialResult() or '{}').get('partial', '')
                    if woken and words != last:
                        since = time.monotonic()    # the half-heard moves on: the wait restarts
                    last = words
                    if woken and time.monotonic() - since > WAKE_AFTER:
                        # the name passed by and quiet followed: her turn to greet
                        return {'woke': True, 'text': ''}
                    if not words.strip():
                        continue
                    hit = wake_at(words, spellings)
                    if not woken and hit is not None:
                        woken = True                # she hears her name: keep on recording
                        since = time.monotonic()
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
                               'recorder': recorder_cmd()[0]})
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
            try:
                res = self.ears.wake(lang, word, secs, size=size, mic=mic)
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
        try:
            text = self.ears.listen(lang, secs, quiet=min(6.0, secs), size=size, mic=mic)
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

#!/usr/bin/env python3
"""Her ears: a local Vosk over the microphone as a tiny HTTP server (no GTK, no Yumi imports).

Yumi's app side (yumi/ears.py) starts this with a Python that has vosk (see
tools/stt_setup.sh) and asks it to listen: /listen?secs=8&lang=ru. The small models
(~45 MB each, Russian and English) are downloaded on the first use into
~/.local/share/yumi/stt; after that a phrase takes a moment.

The microphone is read with whatever the desktop has: parecord, pw-record or arecord.
A click on her mic button starts a listen; vosk hears the end of the phrase, or
secs pass, or another click asks /stop. /wake is the same microphone held on longer:
vosk waits for the wake word (юми), and the moment it passes by the app gets the
phrase that follows — nothing is recognized twice.
"""
import argparse
import atexit
import json
import os
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
MODELS = {                              # lang: (zip on alphacephei.com, folder inside it)
    'ru': ('vosk-model-small-ru-0.22', 'vosk-model-small-ru-0.22'),
    'en': ('vosk-model-small-en-us-0.15', 'vosk-model-small-en-us-0.15'),
}
VERSION = 3                             # bumped when the protocol changes: stale twins are restarted
WAKE_AFTER = 0.7                        # s of quiet after the name before she asks what you wanted
# which command reads the mic, raw s16 mono RATE. pw-record before parecord: on some
# desktops parecord connects and then stays silent, so arecord gets the last word.
RECORDER = [
    ('pw-record', ['pw-record', '--raw', '--format', 's16', '--rate', str(RATE),
                   '--channels', '1', '-']),
    ('arecord', ['arecord', '-q', '-t', 'raw', '-f', 'S16_LE', '-r', str(RATE), '-c', '1', '-']),
    ('parecord', ['parecord', '--raw', '--format=s16le', f'--rate={RATE}',
                  '--channels=1', '--latency-msec=100', '-']),
]


def recorder_cmd():
    for name, cmd in RECORDER:
        if shutil.which(name):
            return name, cmd
    return None, None


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
    """True when the two words differ by at most one letter (the hearsay of Vosk)"""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    # a plain edit distance, cut short: one substitution, insertion or deletion is fine
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
            if min(cur) > 1 and min(prev) > 1:
                return False
        prev = cur
    return prev[-1] <= 1


def wake_at(text, spellings):
    """the place of the wake word in the phrase, or -1; only whole words count
    and only as the first or alone: «юмизнает» must not wake her"""
    toks = [t.strip('.,!?"«»…:;()[]-—') for t in text.lower().replace('ё', 'е').split()]
    for i, t in enumerate(toks):
        if len(t) > 1 and i == 0 and any(near(t, s) for s in spellings):
            return i
    return -1


class Ears:
    """downloads the models one by one and answers one listen at a time"""

    def __init__(self):
        self.models = {}
        self.error = None
        self.busy = False
        self.abort = False
        self.kind = None                # what the lock holds now: 'listen' or 'wake'
        self.lock = threading.Lock()

    def model(self, lang):
        """the recognizer's factory: the model folder, downloaded on the first need"""
        from vosk import Model
        with self.lock:
            if lang in self.models:
                return self.models[lang]
            zip_name, folder = MODELS.get(lang, MODELS['ru'])
            path = HOME / folder
            if not path.exists():
                download(zip_name, folder)
            m = Model(str(path))
            self.models[lang] = m
            return m

    def listen(self, lang, secs, quiet):
        """hear one phrase: the recognized text ('' when there was nothing)"""
        from vosk import KaldiRecognizer
        import selectors
        name, cmd = recorder_cmd()
        if not name:
            raise RuntimeError('no parecord, pw-record or arecord for the microphone')
        model = self.model(lang)
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
                proc.terminate()
                self.busy = False

    def wake(self, lang, word, secs):
        """wait for the wake word, keeping the microphone on:
        {'woke': True, 'text': the phrase after it} — the name was heard, the rest came
        {'woke': True, 'text': ''} — the name alone, a phrase may still come
        {'woke': False, ...} — quiet the whole 'secs', or called off by /stop, or busy.
        'text' is what was said after the wake word: the app recognizes nothing twice."""
        from vosk import KaldiRecognizer
        import selectors
        name, cmd = recorder_cmd()
        if not name:
            raise RuntimeError('no parecord, pw-record or arecord for the microphone')
        model = self.model(lang)
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
                    i = wake_at(words, spellings)
                    if not woken and i >= 0:
                        woken = True                # she hears her name: keep on recording
                        since = time.monotonic()
                    if not woken or not ended:
                        continue    # not her name yet, or the phrase is still forming
                    said = ' '.join(words.split()[i + 1:]) if i >= 0 else words
                    if said.strip():
                        return {'woke': True, 'text': ' '.join(said.split())}
                    since = time.monotonic()        # the name alone: a pause for what follows
                if woken:
                    return {'woke': True, 'text': ''}   # the name was heard, the sound went on
                return {'woke': False, 'text': None}
            finally:
                proc.terminate()
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
            try:
                res = self.ears.wake(lang, word, secs)
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
        try:
            text = self.ears.listen(lang, secs, quiet=min(6.0, secs))
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

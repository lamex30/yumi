#!/usr/bin/env python3
"""Her real voice: Silero TTS as a tiny local HTTP server (no GTK, no Yumi imports).

Yumi's app side (yumi/tts.py) starts this with a Python that has torch (see
tools/tts_setup.sh) and asks it for WAVs: /tts?text=...&vol=0.5. The first start
downloads the voices (~100 MB), after that a line takes a moment. The models live in
~/.local/share/yumi/tts; the speakers and the pitch are tweaked in ~/.config/yumi/tts.json:
{"ru_speaker": "kseniya", "en_speaker": "en_0", "pitch": 1.15}   (1.0 = as recorded)

The Russian model is "v5_6_ru" (questions and exclamations); write "ru_model": "v4_ru"
into the same tts.json to roll back — both models live in the cache side by side, no
re-download needed. Speakers, Russian: kseniya, baya, xenia, aidar, eugene; English: en_0 … en_117.
"""
import argparse
import atexit
import io
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
import wave
from hashlib import sha1
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MODELS_HOME = Path(os.environ.get('YUMI_TTS_HOME') or Path.home() / '.local/share/yumi/tts')
CONF = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'yumi' / 'tts.json'
STATE = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'yumi' / 'tts' / 'port.json'
HUB = 'snakers4/silero-models'
LANGS = {'ru': ('v4_ru', 24000), 'en': ('v3_en', 24000)}   # fallback tags and sample rates
DEFAULTS = {'ru_speaker': 'kseniya', 'en_speaker': 'en_0', 'pitch': 1.15,
            'ru_model': 'v5_6_ru'}   # «ru_model»: «v4_ru» in tts.json rolls the voice back
MAX_TEXT = 500


def log(msg):
    print(f'[yumi-tts] {msg}', flush=True)


def lang_of(text):
    """'ru' when the line has Cyrillic, else 'en'"""
    return 'ru' if any('\u0400' <= c <= '\u04ff' for c in text) else 'en'


class Engine:
    """loads the models one by one and answers one synthesis at a time"""

    def __init__(self, conf):
        self.conf = conf
        self.models = {}
        self.lock = threading.Lock()      # one synthesis at a time
        self.error = None
        self.ready = False
        threading.Thread(target=self.warm, daemon=True).start()

    def warm(self):
        """import torch and load both voices in the background, so the first line is not slow"""
        try:
            for lang in LANGS:
                self.synth('Привет!' if lang == 'ru' else 'Hi!', lang, 0.0)
            self.ready = True
            log('ready')
        except Exception as e:            # noqa: BLE001 - any failure is reported over /ping
            self.error = str(e)
            log(f'warm-up failed: {e}')

    def model(self, lang):
        if lang not in self.models:
            import torch
            torch.hub.set_dir(str(MODELS_HOME / 'hub'))
            tag = self.conf.get('ru_model') if lang == 'ru' else LANGS[lang][0]
            tags = [tag] if tag == LANGS[lang][0] else [tag, LANGS[lang][0]]
            last = None
            for tag in tags:           # a model that would not come: the previous one stays
                try:
                    log(f'loading the {lang} voice ({tag})...')
                    model, _ = torch.hub.load(HUB, 'silero_tts', language=lang, speaker=tag,
                                              device='cpu', trust_repo=True, skip_validation=True)
                    self.models[lang] = model
                    log(f'the {lang} voice is loaded ({tag})')
                    return model
                except Exception as e:  # noqa: BLE001
                    last = e
                    log(f'could not load {tag}: {e}')
            raise last
        return self.models[lang]

    def synth(self, text, lang, vol):
        with self.lock:
            model = self.model(lang)
            speaker = self.conf[f'{lang}_speaker']
            sr = LANGS[lang][1]
            kw = {'put_accent': True, 'put_yo': True} if lang == 'ru' else {}
            try:
                audio = model.apply_tts(text, speaker=speaker, sample_rate=sr, **kw)
            except TypeError:             # an older model without the accent marks
                audio = model.apply_tts(text, speaker=speaker, sample_rate=sr)
            return to_wav(audio, sr, self.conf['pitch'], vol)


def to_wav(audio, rate, pitch, vol):
    """float audio -> 16-bit mono WAV; a pitch above 1.0 makes her smaller and cuter"""
    import numpy as np
    a = audio.detach().cpu().numpy().astype('float32').squeeze()
    if pitch > 1.001:                 # played a touch faster, hence higher
        n = max(1, int(len(a) / pitch))
        a = np.interp(np.linspace(0.0, len(a) - 1.0, n), np.arange(len(a)), a)
    a = np.clip(a * 0.85 * max(0.0, min(1.0, vol)), -1.0, 1.0)
    out = io.BytesIO()
    with wave.open(out, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((a * 32767.0).astype('<i2').tobytes())
    return out.getvalue()


class Handler(BaseHTTPRequestHandler):
    engine = None
    profile = ''

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == '/ping':
            return self._json({'ok': True, 'ready': self.engine.ready, 'error': self.engine.error,
                               'profile': self.profile})
        if u.path != '/tts':
            return self._json({'error': 'no such path'}, 404)
        text = ' '.join((q.get('text') or [''])[0].split())[:MAX_TEXT]
        if not text:
            return self._json({'error': 'no text'}, 400)
        try:
            vol = float((q.get('vol') or ['0.5'])[0])
        except ValueError:
            vol = 0.5
        lang = (q.get('lang') or [''])[0].strip().lower()
        if lang not in LANGS:
            lang = lang_of(text)          # not told: guess it by the letters
        try:
            data = self.engine.synth(text, lang, vol)
        except Exception as e:        # noqa: BLE001
            return self._json({'error': str(e)}, 500)
        self.send_response(200)
        self.send_header('Content-Type', 'audio/wav')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_a):       # the log stays for real problems
        pass


def read_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return None


def write_state(port, profile):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({'port': port, 'profile': profile}))


def main():
    ap = argparse.ArgumentParser(description='Yumi\'s real voice: local Silero TTS over HTTP')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--warmup', action='store_true', help='load the voices and exit (for tools/tts_setup.sh)')
    ap.add_argument('--timeout', type=int, default=900, help='warm-up timeout in seconds')
    args = ap.parse_args()

    conf = dict(DEFAULTS)
    try:
        conf.update({k: v for k, v in json.loads(CONF.read_text()).items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    profile = sha1(json.dumps(conf, sort_keys=True).encode()).hexdigest()[:12]
    Handler.engine = Engine(conf)
    Handler.profile = profile

    if args.warmup:
        deadline = time.time() + args.timeout
        while not (Handler.engine.ready or Handler.engine.error or time.time() > deadline):
            time.sleep(0.5)
        if Handler.engine.error:
            sys.exit(1)
        return

    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    except OSError:                   # the port is taken: an older twin may already be there
        info = read_state()
        if info and info.get('profile') == profile and ping(info['port']):
            write_state(info['port'], profile)
            log(f'already running on port {info["port"]}')
            return
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    port = server.server_address[1]
    write_state(port, profile)
    atexit.register(try_forget, port)
    log(f'listening on 127.0.0.1:{port} (profile {profile})')
    server.serve_forever()


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


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass

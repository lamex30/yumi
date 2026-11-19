#!/usr/bin/env python3
"""Her real voice: the client side against a fake daemon. Run: python3 tests/test_tts.py"""
import io
import json
import os
import sys
import tempfile
import threading
import urllib.parse
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

tmp = tempfile.mkdtemp()
os.environ['XDG_CACHE_HOME'] = tmp          # her cache must be redirected before the import
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ['YUMI_TTS_PY'] = '/nonexistent'  # she must not start a real daemon here

from yumi import config, tts  # noqa: E402

HITS = []


def tiny_wav(seconds=0.3):
    out = io.BytesIO()
    with wave.open(out, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b'\0\0' * int(24000 * seconds))
    return out.getvalue()


class Fake(BaseHTTPRequestHandler):
    def _send(self, data, code=200, ctype='application/json'):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u, q = urllib.parse.urlparse(self.path), urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if u.path == '/ping':
            return self._send(json.dumps({'ok': True, 'ready': True, 'error': None,
                                          'profile': 'testprofile'}).encode())
        if u.path == '/tts':
            HITS.append((q['text'][0], q['vol'][0]))
            return self._send(tiny_wav(), ctype='audio/wav')
        self._send(b'{}', 404)

    def log_message(self, *_a):
        pass


server = ThreadingHTTPServer(('127.0.0.1', 0), Fake)
threading.Thread(target=server.serve_forever, daemon=True).start()
tts.DIR.mkdir(parents=True, exist_ok=True)
(tts.DIR / 'port.json').write_text(json.dumps({'port': server.server_address[1], 'profile': 'x'}))

p = tts.speak('Привет!', 0.5)
assert p and p.exists() and p.parent == tts.DIR, p
assert tts.wav_len(p) and abs(tts.wav_len(p) - 0.3) < 0.05, tts.wav_len(p)
p2 = tts.speak('Привет!', 0.5)
assert p2 == p, 'the same line must come from the cache'
assert len(HITS) == 1, HITS
assert tts.speak('Привет!', 0.25) != p, 'a different volume is a different line'
assert HITS[0] == ('Привет!', '0.5'), HITS[0]

# digits become words before they reach the daemon: Silero reads «четырнадцать часов» well
assert tts._convert_digits_for_tts('Сейчас 14:00.') == 'Сейчас четырнадцать часов.'
assert tts._convert_digits_for_tts('Сейчас 14:05.') == 'Сейчас четырнадцать часов пять минут.'
assert tts._convert_digits_for_tts('Время — 9:21.') == 'Время — девять часов двадцать одна минута.'
assert tts._convert_digits_for_tts("It's 2:14 pm.") == "It's two fourteen p m."
assert tts._convert_digits_for_tts('В году 2026') == 'В году две тысячи двадцать шесть'
p3 = tts.speak('Сейчас 14:05.', 0.5)
assert p3 and HITS[-1][0] == 'Сейчас четырнадцать часов пять минут.', HITS[-1]

# her stress dictionary: the user's «+» reaches the daemon inside the whole word
tts.STRESS = Path(tmp) / 'stress.json'
tts.STRESS.write_text(json.dumps({'торты': 'т+орты', 'тортов': 'т+ортов'}, ensure_ascii=False),
                      encoding='utf-8')
tts._stress_cache = None
assert tts.apply_stress('Торты! Все тортов съела.') == 'т+орты! Все т+ортов съела.'
assert tts.apply_stress('не тортыво') == 'не тортыво', 'only whole words may be touched'
assert tts.apply_stress('Hi there!') == 'Hi there!', 'english lines stay as they are'
assert tts.speak('Торты', 0.5) and HITS[-1][0] == 'т+орты', HITS[-1]
p_cached = tts.speak('Торты', 0.5)
assert p_cached and len(HITS) == 4, HITS  # the stressed line is cached like any other
# the file may be edited while she is awake: the next line follows the new word
tts.STRESS.write_text(json.dumps({'звонит': 'звон+ит'}, ensure_ascii=False), encoding='utf-8')
tts._stress_cache = None
assert tts.apply_stress('звонит') == 'звон+ит'

# a stale port file: nothing answers, and starting a daemon is impossible -> None (blips instead)
server.shutdown()
(tts.DIR / 'port.json').write_text('{"port": 1, "profile": "x"}')
assert tts.speak('Привет!', 0.5) is None

# the cache stays under the limit, the oldest go first
(tts.DIR / 'port.json').unlink()
old = tts.DIR / 'aaa.wav'
new = tts.DIR / 'zzz.wav'
old.write_bytes(b'\0' * 100)
new.write_bytes(b'\0' * 100)
os.utime(old, (1, 1))
tts.LIMIT = 150
tts._evict()
assert not old.exists() and new.exists(), 'the oldest file must go'
tts.LIMIT = 256 * 1024 * 1024

# the voice profile: settings and tts.json stay one thing; no real restart here
tts.CONF = Path(tmp) / 'tts.json'
restarts = []
tts.restart = lambda: restarts.append('restart') or True
settings = dict(config.DEFAULT_SETTINGS)
settings['tts_ru_speaker'] = 'baya'
settings['tts_pitch'] = 1.25
want = tts.profile_from_settings(settings)
assert want['ru_speaker'] == 'baya' and want['pitch'] == 1.25, want
first = tts.sync_profile(settings)
assert first['changed'] is True and restarts == ['restart'], first
saved = json.loads(tts.CONF.read_text())
assert saved['ru_speaker'] == 'baya' and saved['pitch'] == 1.25, saved
again = tts.sync_profile(settings)
assert again['changed'] is False and restarts == ['restart'], 'a plain save must not relaunch the voice'
assert tts.current_profile()['ru_speaker'] == 'baya'

# the panel's cheap status reads the same tts.json and speaks the daemon's key names
from yumi import engines  # noqa: E402
engines.TTS_CONF = tts.CONF
profile = engines.tts_profile()
assert profile['ru_speaker'] == 'baya' and profile['pitch'] == 1.25, profile

print('ok')

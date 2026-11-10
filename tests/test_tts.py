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

from yumi import tts  # noqa: E402

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

print('ok')

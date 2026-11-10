#!/usr/bin/env python3
"""The little questions she answers without her mind. Run: python3 tests/test_chat.py"""
import importlib.util
import re
import sys
import threading
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('phrases', _root / 'yumi' / 'phrases.py')
phrases = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phrases)
sys.path.insert(0, str(_root))
from yumi import chat  # noqa: E402

# the clock questions she recognizes
for t in ('Который час?', 'сколько времени', 'Юми, какое сейчас время?', 'сколько уже времени',
          "what time is it", "What's the time?", "what is the time", "how late is it"):
    assert chat.time_question(t), t
for t in ('какая погода', 'открой файл', 'привет', 'set a timer'):
    assert not chat.time_question(t), t

# the clock as she says it: 24 hours in Russian, sleepy lowercase am/pm in English
assert re.fullmatch(r'\d{2}:\d{2}', chat.the_time('ru'))
assert re.fullmatch(r'\d{1,2}:\d{2} (am|pm)', chat.the_time('en'))
t = chat.the_time('en', now=1)          # 00:00 hours start at midnight somewhere; just shape
assert t.endswith('am') or t.endswith('pm'), t

# weather: not a weather phrase at all, a place, or her own window
assert chat.weather_question('который час') is None
assert chat.weather_question('какая погода?') == ''
assert chat.weather_question('какая погода в Москве?') == 'москве'
assert chat.weather_question('что там за окном?') == ''
assert chat.weather_question("what's the weather in San Francisco tonight?") == 'san francisco'
assert chat.weather_question('is it raining at your place') == 'your place'
assert chat.weather_question('покажи погоду в Санкт-Петербурге') == 'санкт-петербурге'

# her lines format cleanly when the clock or the window answers
text, _face = phrases.Speech({'speech_lang': 'ru'}).line('chat_time', time='14:32')
assert '14:32' in text and '{' not in text, text
text, _face = phrases.Speech({'speech_lang': 'ru'}).line('weather', report='+4°C, Пасмурно')
assert '+4°C' in text and '{' not in text, text

# Polza.ai: the OpenAI-compatible endpoint answers with a string, the key travels along
import json as _json                                   # noqa: E402
import urllib.request as _urllib                        # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer as _Server  # noqa: E402

SEEN = {}


class Polza(BaseHTTPRequestHandler):
    def do_POST(self):
        SEEN['path'] = self.path
        SEEN['auth'] = self.headers.get('Authorization')
        body = _json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        SEEN['model'] = body.get('model')
        SEEN['max_tokens'] = body.get('max_tokens')
        out = _json.dumps({'choices': [{'message': {'role': 'assistant',
                                                    'content': '  Спокойной ночи!  '}}]}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *_a):
        pass


polza = _Server(('127.0.0.1', 0), Polza)
threading.Thread(target=polza.serve_forever, daemon=True).start()
s = {'chat_provider': 'polza.ai', 'polza_url': f'http://127.0.0.1:{polza.server_address[1]}/v1',
     'polza_api_key': 'secret', 'chat_model': 'deepseek/deepseek-v4-flash'}
assert chat.alive(s)
assert chat.models(s) == ['deepseek/deepseek-v4-flash']
reply = chat.ask(s, [{'role': 'user', 'content': 'привет'}])
assert reply == 'Спокойной ночи!', reply            # a string, not a list of words
assert SEEN['path'] == '/v1/chat/completions', SEEN
assert SEEN['auth'] == 'Bearer secret', SEEN
assert SEEN['model'] == 'deepseek/deepseek-v4-flash' and SEEN['max_tokens'] == 220, SEEN
assert chat.hourly_joke(s, 14) == 'Спокойной ночи!'
assert chat.ask({'chat_provider': 'polza.ai', 'polza_url': 'http://127.0.0.1:1/x'}, []) == ''
polza.shutdown()

# no key: she gives up at once, no hanging request, no key sent anywhere
assert chat.ask({'chat_provider': 'polza.ai'}, [{'role': 'user', 'content': 'hi'}]) == ''
assert chat.hourly_joke({'chat_provider': 'polza.ai'}, 14) == ''

print('ok')

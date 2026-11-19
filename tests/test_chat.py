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
          "what time is it", "What's the time?", "what is the time", "how late is it",
          'время', 'Время!', 'времени', 'скажи время', 'а время сейчас', 'времечко',
          'time'):
    assert chat.time_question(t), t
for t in ('какая погода', 'открой файл', 'привет', 'set a timer',
          'рабочее время', 'время летит', 'не хватает времени'):
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

# the forecast slice: today, tomorrow or the whole week
assert chat.weather_when('какая погода в Москве?') == 'today'
assert chat.weather_when('юми погода на завтра в ялте') == 'tomorrow'
assert chat.weather_when('погода завтра') == 'tomorrow'
assert chat.weather_when('погода на неделю') == 'week'
assert chat.weather_when('what is the weather this week in London') == 'week'
assert chat.weather_when('is it raining at your place') == 'today'

# the place survives the forecast words
assert chat.weather_question('покажи погоду в Санкт-Петербурге на завтра') == 'санкт-петербурге'
assert chat.weather_question('погода в ялте на неделю') == 'ялте'
assert chat.weather_question('погода на неделю в Ялте') == 'ялте'
assert chat.weather_question('погода на завтра') == ''

# the sky in words, for the codes Open-Meteo sends back
assert chat.wmo_desc(0, 'ru') == 'ясно' and chat.wmo_desc(3, 'en') == 'overcast', chat.wmo_desc(3)

# the lines she says about tomorrow and the week (no net needed: pure formatting)
assert chat._fmt_tomorrow('Ялта', {'code': 3, 'min': 4.2, 'max': 9.7}, 'ru') == \
    'Завтра, Ялта: пасмурно, от +4 до +10°C'
assert chat._fmt_tomorrow('Yalta', {'code': 0, 'min': -3.4, 'max': 2.1}, 'en') == \
    'Tomorrow, Yalta: clear, -3 to +2 °C'
assert chat._fmt_week('Ялта', [{'date': '2026-11-11', 'min': 4, 'max': 9},
                               {'date': '2026-11-12', 'min': 6, 'max': 11}], 'ru') == \
    'Ялта, на неделю: ср +4/+9°, чт +6/+11°'

# the signs and the degrees become words for her ears (the bubble keeps the signs)
assert chat.weather_speak('+5°C, Облачно', 'ru') == 'плюс 5 градусов, Облачно'
assert chat.weather_speak('Ялта: -3°C', 'ru') == 'Ялта: минус 3 градуса'
assert chat.weather_speak('+1°C', 'ru') == 'плюс 1 градус'
assert chat.weather_speak('+2°C', 'ru') == 'плюс 2 градуса'
assert chat.weather_speak('+21°C', 'ru') == 'плюс 21 градус'
assert chat.weather_speak('+11°C', 'ru') == 'плюс 11 градусов'
assert chat.weather_speak('Ялта, на неделю: ср +8/+14°, чт -3/-1°', 'ru') == \
    'Ялта, на неделю: ср от плюс 8 до плюс 14 градусов, чт от минус 3 до минус 1 градус'
assert chat.weather_speak('Yalta: +5°C, Light rain', 'en') == 'Yalta: 5 degrees, Light rain'
assert chat.weather_speak('Yalta: -3°C', 'en') == 'Yalta: minus 3 degrees'
assert chat.weather_speak('Yalta, this week: Wed +8/+14°', 'en') == \
    'Yalta, this week: Wed 8 to 14 degrees'
assert chat.weather_speak('Завтра, Ялта: пасмурно, от +4 до +10°C', 'ru') == \
    'Завтра, Ялта: пасмурно, от плюс 4 до плюс 10 градусов'

# her lines format cleanly when the clock or the window answers
text, _face = phrases.Speech({'speech_lang': 'ru'}).line('chat_time', time='14:32')
assert '14:32' in text and '{' not in text, text
text, _face = phrases.Speech({'speech_lang': 'ru'}).line('weather', report='+4°C, Пасмурно')
assert '+4°C' in text and '{' not in text, text

# the joke command: her mind makes one up on the spot
for t in ('анекдот', 'расскажи анекдот', 'расскажи шутку', 'Юми, пошути',
          'скажи что-нибудь смешное', 'tell me a joke', 'make me laugh'):
    assert chat.joke_question(t), t
for t in ('который час', 'какая погода', 'привет', 'set a timer'):
    assert not chat.joke_question(t), t

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
assert chat.joke(s, 'ru') == 'Спокойной ночи!'
assert chat.ask({'chat_provider': 'polza.ai', 'polza_url': 'http://127.0.0.1:1/x'}, []) == ''
polza.shutdown()

# no key: she gives up at once, no hanging request, no key sent anywhere
assert chat.ask({'chat_provider': 'polza.ai'}, [{'role': 'user', 'content': 'hi'}]) == ''
assert chat.hourly_joke({'chat_provider': 'polza.ai'}, 14) == ''
assert chat.joke({'chat_provider': 'polza.ai'}, 'ru') == ''

# the question must reach Ollama: chat_ask's worker walks a COPY of the history —
# an assignment to `history` inside work() makes every earlier read of it an
# UnboundLocalError, and every chat question dies before the answer
src = (_root / 'yumi' / 'ui' / 'senses.py').read_text(encoding='utf-8')
assert 'hist = list(history)' in src, 'chat_ask must walk a copy of the history'
assert 'history = list(history)' not in src, \
    'rebinding history inside work() crashes every read of it (UnboundLocalError)'

print('ok')

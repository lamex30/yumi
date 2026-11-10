#!/usr/bin/env python3
"""The little questions she answers without her mind. Run: python3 tests/test_chat.py"""
import importlib.util
import re
import sys
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

print('ok')

#!/usr/bin/env python3
"""Russian number forms in her lines. Run: python3 tests/test_phrases.py"""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('phrases', Path(__file__).resolve().parent.parent / 'yumi' / 'phrases.py')
phrases = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phrases)

want = {1: 'файл', 2: 'файла', 4: 'файла', 5: 'файлов', 10: 'файлов', 11: 'файлов', 12: 'файлов', 14: 'файлов',
        20: 'файлов', 21: 'файл', 22: 'файла', 25: 'файлов', 100: 'файлов', 101: 'файл', 111: 'файлов', 112: 'файлов',
        121: 'файл', 122: 'файла'}
for n, word in want.items():
    assert phrases.plural_ru(n, 'файл', 'файла', 'файлов') == word, n

speech = phrases.Speech({'speech_lang': 'ru'})
for _ in range(9):
    text, _face = speech.line('drop_many', n=3)
    assert '{' not in text, text
    assert '3 файла' in text or 'куча' in text, text

# the startup hello matches the time of day (the clock is faked, the rng is told
# to always pick the time-of-day lines)
import types
class _AlwaysTime:                      # a rng that keeps the time-of-day lines on top
    def random(self):
        return 0.0
    def choice(self, seq):
        return seq[-1]
for hour, greetings in [(9, ('Доброе утро', 'утро уже')),
                        (14, ('Добрый день', 'День.')),
                        (20, ('Добрый вечер', 'Вечер.')),
                        (2, ('Доброй ночи', 'Ночь.'))]:
    part = phrases.time_of_day(hour)
    phrases.time = types.SimpleNamespace(          # fake the wall clock
        localtime=lambda h=hour: types.SimpleNamespace(tm_hour=h),
        strftime=lambda f: '10:07')
    got = set()
    for _ in range(12):
        speech = phrases.Speech({'speech_lang': 'ru'}, rng=_AlwaysTime())
        text, _face = speech.line('hello')
        assert '{' not in text and '{hhmm' not in text, text
        got.add(text)
    assert all(any(g in t for g in greetings) for t in got), (hour, sorted(got))
phrases.time = __import__('time')                  # restore

# the startup hello comes even while Do Not Disturb is on, an ordinary one does not
speech = phrases.Speech({'speech_lang': 'ru'})
assert speech.say('hello', asleep=True) is None
assert speech.say('hello', asleep=True, force=True) is not None
print('ok')

#!/usr/bin/env python3
"""The shared voice entities — volume, time of day and named days.

Run: python3 tests/test_entities.py
"""
import sys
from datetime import date, timedelta
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
from yumi import entities  # noqa: E402

# a digit, a spelled-out ten, and a lone one (the tens are all she obeys)
assert entities.spoken_number('50') == 50
assert entities.spoken_number('50%') == 50
assert entities.spoken_number('пятьдесят') == 50
assert entities.spoken_number('пять') == 50
assert entities.spoken_number('nine') == 90
assert entities.spoken_number('_disable') is None

# one lone number after the ears dropped the rest
assert entities.bare_volume('30') == 30
assert entities.bare_volume('тридцать') == 30
assert entities.bare_volume('пять') == 50
assert entities.bare_volume('громкость тридцать') is None
assert entities.bare_volume('сколько два плюс два') is None

# whole levels, steps and a number beside the word for volume
assert entities.volume('максимум') == {'value': 100, 'raw': 'максимум'}
assert entities.volume('минимум')['value'] == 0
assert entities.volume('половина')['value'] == 50
assert entities.volume('half')['value'] == 50
assert entities.volume('громкости 30')['value'] == 30
assert entities.volume('volume fifty')['value'] == 50
assert entities.volume('тише') == {'value': -10, 'raw': 'тише', 'relative': True}
assert entities.volume('какая погода') is None

# the clock and the named days
assert entities.time_of_day('утром') == {'value': '08:00', 'raw': 'утром'}
assert entities.time_of_day('вечером')['value'] == '18:00'
assert entities.time_of_day('полдень')['value'] == '12:00'
assert entities.time_of_day('midnight')['value'] == '00:00'
assert entities.time_of_day(' Maintenance ') is None

tomorrow = entities.relative_date('до встречи завтра')
assert tomorrow['value'] == 'tomorrow' and tomorrow['offset'] == 1
assert tomorrow['date'] == (date.today() + timedelta(days=1)).isoformat()
assert entities.relative_date('вчера')['value'] == 'yesterday'
assert entities.relative_date('сегодня')['offset'] == 0
assert entities.relative_date('обычный день') is None

found = entities.extract('сделай громкость половина и покажи завтра вечером')
assert set(found) == {'volume', 'time', 'date'}, found
assert found['volume']['value'] == 50
assert found['time']['value'] == '18:00'
assert found['date']['value'] == 'tomorrow'

print('tests/test_entities.py: ok')

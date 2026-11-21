"""The small values behind voice commands, shared by the rules and the skills.

A voice phrase may name a few built-in entities (volume, time, date); each has
its spellings and one normal form.  Yumi already had the same numbers scattered
between the music skill and the chat; this module is the one quiet place for
them.  It imports neither GTK nor the brain (the brain imports it instead), so
the rule engine, the skills and the tests can all read the same answer.

    volume('громкость половина') -> {'value': 50, 'raw': 'половина'}
    time_of_day('утром')         -> {'value': '08:00', 'raw': 'утром'}
    relative_date('завтра')      -> {'value': 'tomorrow', 'offset': 1, ...}
"""
from datetime import date, timedelta

VOLUME_NUMBERS = {'десять': 10, 'двадцать': 20, 'тридцать': 30, 'сорок': 40,
                  'пятьдесят': 50, 'шестьдесят': 60, 'семьдесят': 70,
                  'восемьдесят': 80, 'девяносто': 90, 'сто': 100,
                  'ten': 10, 'twenty': 20, 'thirty': 30, 'forty': 40,
                  'fifty': 50, 'sixty': 60, 'seventy': 70, 'eighty': 80,
                  'ninety': 90}
VOLUME_ONES = {'один': 10, 'одна': 10, 'два': 20, 'две': 20, 'три': 30,
               'четыре': 40, 'пять': 50, 'шесть': 60, 'семь': 70,
               'восемь': 80, 'девять': 90, 'one': 10, 'two': 20, 'three': 30,
               'four': 40, 'five': 50, 'six': 60, 'seven': 70, 'eight': 80,
               'nine': 90}   # «громкость пять» means fifty: only the tens she obeys
VOLUME_WORD = frozenset(('громкость', 'звук', 'volume', 'sound'))

# the words that stand for a whole level, and the words that ask for a step
VOLUME_WHOLE = {'максимум': 100, 'максимально': 100, 'полный': 100,
                'полная': 100, 'max': 100, 'maximum': 100,
                'минимум': 0, 'минимально': 0, 'min': 0, 'minimum': 0,
                'ноль': 0, 'zero': 0, 'половина': 50, 'половинка': 50,
                'half': 50}
VOLUME_STEP = {'громче': 10, 'повысь': 10, 'louder': 10, 'up': 10,
               'тише': -10, 'потише': -10, 'quieter': -10, 'lower': -10}

TIME_WORDS = {'утро': '08:00', 'утром': '08:00', 'morning': '08:00',
              'день': '12:00', 'днем': '12:00', 'днём': '12:00', 'noon': '12:00',
              'полдень': '12:00', 'полдня': '12:00', 'midday': '12:00',
              'вечер': '18:00', 'вечером': '18:00', 'evening': '18:00',
              'ночь': '23:00', 'ночью': '23:00', 'night': '23:00',
              'полночь': '00:00', 'midnight': '00:00'}

# value, days from today; the ISO date is filled in when the entity is read
DATE_WORDS = {'сегодня': ('today', 0), 'завтра': ('tomorrow', 1),
              'послезавтра': ('day_after_tomorrow', 2),
              'вчера': ('yesterday', -1), 'позавчера': ('day_before_yesterday', -2),
              'today': ('today', 0), 'tomorrow': ('tomorrow', 1),
              'yesterday': ('yesterday', -1)}

_CLIP = '.,!?"«»…:;()[]-—’\''


def tokens(text):
    """the words of a phrase the entities read: lower case, ё folded into е"""
    return [t.strip(_CLIP).lower().replace('ё', 'е')
            for t in str(text or '').split() if t.strip(_CLIP)]


def spoken_number(t):
    """the level a word or a digit stands for: «50%», «пятьдесят», «fifty»;
    a lone one («пять», «5») is read as a ten, the tens are all she obeys"""
    t = str(t or '').strip().lower().replace('ё', 'е')
    if t.rstrip('%').isdigit():
        n = int(t.rstrip('%'))
    else:
        n = VOLUME_NUMBERS.get(t) or VOLUME_ONES.get(t)
    if n is not None and 0 < n < 10:
        n *= 10
    return n


def bare_volume(text):
    """one lone number said to her (the ears kept «30» and dropped the rest):
    the volume level that most likely was meant (10..100), or None"""
    toks = tokens(text)
    if len(toks) != 1:
        return None
    n = spoken_number(toks[0])
    if n is None or n <= 0:
        return None
    return min(100, max(10, n))


def volume(text, lang='ru'):
    """the level named in a phrase, or None: a whole word («максимум»), a step
    («тише»), or a number beside the word for volume («громкости 30»)"""
    toks = tokens(text)
    for i, t in enumerate(toks):
        if t in VOLUME_WHOLE:
            return {'value': VOLUME_WHOLE[t], 'raw': t}
        if t in VOLUME_STEP:
            return {'value': VOLUME_STEP[t], 'raw': t, 'relative': True}
        if t in VOLUME_WORD or t.startswith('громкост'):
            for near in toks[i + 1:i + 3]:
                n = spoken_number(near)
                if n is not None:
                    return {'value': min(100, max(0, n)), 'raw': near}
    return None


def time_of_day(text, lang='ru'):
    """the clock a word stands for: «утро» → 08:00, «вечер» → 18:00"""
    for t in tokens(text):
        if t in TIME_WORDS:
            return {'value': TIME_WORDS[t], 'raw': t}
    return None


def relative_date(text, lang='ru'):
    """a named day: its normal form, its offset from today and its ISO date"""
    for t in tokens(text):
        if t in DATE_WORDS:
            value, offset = DATE_WORDS[t]
            return {'value': value, 'offset': offset,
                    'date': (date.today() + timedelta(days=offset)).isoformat(),
                    'raw': t}
    return None


def extract(text, lang='ru'):
    """{'volume': ..., 'time': ..., 'date': ...} for whatever the phrase names"""
    out = {}
    for key, reader in (('volume', volume), ('time', time_of_day),
                        ('date', relative_date)):
        found = reader(text, lang)
        if found:
            out[key] = found
    return out

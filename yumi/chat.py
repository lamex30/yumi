"""Her chat: answers through a local Ollama or Polza.ai (no GTK here).

She asks http://127.0.0.1:11434 (settings 'ollama_url') with the conversation so far
and answers with the model from settings 'chat_model'
(default lakomoor/vikhr-llama-3.2-1b-instruct:1b, changed in Settings → Her mind).

When settings 'chat_provider' is 'polza.ai', she talks to the Polza.ai OpenAI-compatible
endpoint (settings 'polza_url', 'polza_api_key') instead.

If the provider is not running or has no models, ask() returns '' and she says so.
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_MODEL = 'lakomoor/vikhr-llama-3.2-1b-instruct:1b'
DEFAULT_URL = 'http://127.0.0.1:11434'
POLZA_MODEL = 'deepseek/deepseek-v4-flash'


def _provider(settings):
    return str(settings.get('chat_provider') or 'ollama')


def _url(settings):
    if _provider(settings) == 'polza.ai':
        return str(settings.get('polza_url') or 'https://polza.ai/api/v1').rstrip('/')
    return str(settings.get('ollama_url') or DEFAULT_URL).rstrip('/')


def _post(url, body, timeout):
    req = urllib.request.Request(url, data=body,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def alive(settings, timeout=0.8):
    """is there an Ollama / Polza.ai on the other end"""
    try:
        if _provider(settings) == 'polza.ai':
            return True   # cannot cheaply ping Polza.ai without a key; assume yes
        return isinstance(_get(_url(settings) + '/api/version', timeout), dict)
    except (OSError, ValueError):
        return False


def _get(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def models(settings):
    """installed model names, sorted; [] when the provider is not reachable"""
    try:
        if _provider(settings) == 'polza.ai':
            return [POLZA_MODEL]
        data = _get(_url(settings) + '/api/tags', timeout=4)
    except (OSError, ValueError):
        return []
    out = {str(m.get('name')) for m in data.get('models') or [] if m.get('name')}
    return sorted(out)


def system_prompt(lang):
    """who she is: short answers, her language, her sleepy character"""
    if str(lang) == 'ru':
        return ('Ты — Юми, сонная девочка-мотылёк в пиксельном виде, живёшь у нижнего края экрана '
                'и помогаешь с мелочами на компьютере. Отвечай по-русски, одно-два коротких '
                'предложения, дружелюбно и чуть-чуть сонно. Без эмодзи и без списков.')
    return ('You are Yumi, a sleepy pixel-art moth girl living at the bottom edge of the screen, '
            'helping with little things on this computer. Answer in English, one or two short '
            'sentences, friendly and a little sleepy. No emoji, no lists.')


def _ollama_chat(settings, history, timeout):
    body = json.dumps({'model': settings.get('chat_model') or DEFAULT_MODEL,
                       'messages': history, 'stream': False,
                       'options': {'num_predict': 220}}).encode()
    return _post(_url(settings) + '/api/chat', body, timeout)


def _polza_chat(settings, history, timeout):
    body = json.dumps({'model': settings.get('chat_model') or POLZA_MODEL,
                       'messages': history, 'stream': False,
                       'max_tokens': 220}).encode()
    headers = {'Content-Type': 'application/json',
               'Authorization': 'Bearer ' + str(settings.get('polza_api_key') or '')}
    req = urllib.request.Request(_url(settings) + '/chat/completions', data=body,
                                 headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def ask(settings, history, timeout=90):
    """one reply for the conversation `history` ([{role, content}, ...], user last);
    the reply text, or '' if it did not work out"""
    if _provider(settings) == 'polza.ai' and not str(settings.get('polza_api_key') or '').strip():
        return ''          # no key: Polza.ai would hang or refuse, she says so at once
    try:
        if _provider(settings) == 'polza.ai':
            data = _polza_chat(settings, history, timeout)
            msg = (data.get('choices') or [{}])[0].get('message') or {}
            return ' '.join(str(msg.get('content') or '').split())
        data = _ollama_chat(settings, history, timeout)
    except (urllib.error.URLError, OSError, ValueError):
        return ''
    return ' '.join(str((data.get('message') or {}).get('content') or '').split())


# ---- the little questions she needs no mind for: the clock and the window ----

TIME_Q_RE = re.compile(
    r'который\s+час|сколько\s+(?:уже\s+)?времени|какое\s+(?:сейчас\s+)?время'
    r"|what\s+time\s+is\s+it|what(?:'?s|\s+is)\s+the\s+time|how\s+late\s+is\s+it")

WEATHER_Q_RE = re.compile(
    r'\bпогод|за\s+окном|что\s+на\s+улице|как\s+на\s+улице'
    r'|\bweather\b|\bforecast\b|is\s+it\s+(?:raining|snowing)'
    r'|how\s+(?:hot|warm|cold|sunny)')

# words that may trail a place name in a phrase but are not the place itself
TRAILING = {'сегодня', 'сейчас', 'теперь', 'завтра', 'вечером', 'утром', 'днём', 'ночью',
            'now', 'today', 'tonight', 'tomorrow', 'right', 'please', 'outside'}

# the words that name a slice of the forecast («погода на завтра», «what's the weather this week»)
WHEN_RE = re.compile(
    r'\bна\s+(?:завтра|недел[юе]|сегодня|7\s+дней)'
    r'|\b7\s+days\b|\bthis\s+week\b|\bweek\b|\btomorrow\b|\btonight\b|\btoday\b'
    r'|\bзавтра\b|\bнедел[юе]\b|\bсегодня\b')

WEEKDAYS = {'ru': ('пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'),
            'en': ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')}

# the sky in words, for the weather codes Open-Meteo sends back (WMO 4677)
WMO = {
    0: ('ясно', 'clear'), 1: ('почти ясно', 'mostly clear'),
    2: ('переменная облачность', 'partly cloudy'), 3: ('пасмурно', 'overcast'),
    45: ('туман', 'fog'), 48: ('изморозь', 'freezing fog'),
    51: ('морось', 'light drizzle'), 53: ('морось', 'drizzle'), 55: ('морось', 'heavy drizzle'),
    56: ('ледяная морось', 'freezing drizzle'), 57: ('ледяная морось', 'freezing drizzle'),
    61: ('дождик', 'light rain'), 63: ('дождь', 'rain'), 65: ('ливень', 'heavy rain'),
    66: ('ледяной дождь', 'freezing rain'), 67: ('ледяной дождь', 'freezing rain'),
    71: ('снежок', 'light snow'), 73: ('снег', 'snow'), 75: ('снегопад', 'heavy snow'),
    77: ('снежные зёрна', 'snow grains'),
    80: ('ливни', 'rain showers'), 81: ('ливни', 'rain showers'), 82: ('ливни', 'violent showers'),
    85: ('снежные ливни', 'snow showers'), 86: ('снежные ливни', 'snow showers'),
    95: ('гроза', 'thunderstorm'), 96: ('гроза с градом', 'thunderstorm with hail'),
    99: ('гроза с градом', 'thunderstorm with hail'),
}


def time_question(text):
    """True when the phrase asks for the time: her eyes and the clock are enough"""
    t = ' '.join(str(text or '').lower().split()).strip(' ?!.,')
    return bool(TIME_Q_RE.search(t))


def the_time(lang='en', now=None):
    """the clock as she says it: 24 hours for Russian, «2:14 pm» for English"""
    t = time.localtime() if now is None else time.localtime(now)
    if str(lang) == 'ru':
        return time.strftime('%H:%M', t)
    return time.strftime('%I:%M %p', t).lstrip('0').lower()


def weather_question(text):
    """the place to look up from the phrase: a guessed city, '' for wherever she is,
    None when the phrase is not about the weather at all"""
    t = ' '.join(str(text or '').lower().split()).strip(' ?!.,')
    if not WEATHER_Q_RE.search(t):
        return None
    t = WHEN_RE.sub(' ', t)             # «на завтра», «на неделю» и прочие не про место
    # the place runs after the last в/во/in/at until the end of the phrase
    cuts = [(t.rfind(s), s) for s in (' в ', ' во ', ' in ', ' at ')]
    i, sep = max(cuts)
    if i > 0:
        words = t[i + len(sep):].strip(' ?!.,').split()
        while words and words[-1] in TRAILING:
            words.pop()
        if words and len(words) <= 3:
            return ' '.join(words)[:64]
    return ''


def weather_when(text):
    """which slice of the forecast the phrase asks for: 'today', 'tomorrow' or 'week'"""
    t = ' '.join(str(text or '').lower().split())
    if re.search(r'недел|week\b|7\s+дней|7\s+days', t):
        return 'week'
    if re.search(r'\bзавтра\b|tomorrow\b', t):
        return 'tomorrow'
    return 'today'


def hourly_joke(settings, hour, lang='en', timeout=90):
    """ask the mind for a short funny line about this hour (Russian or English),
    one or two sentences, no emoji, no lists; '' when the mind is out of reach"""
    if _provider(settings) == 'polza.ai' and not str(settings.get('polza_api_key') or '').strip():
        return ''          # no key: Polza.ai would hang or refuse, she says so at once
    body = json.dumps({
        'model': settings.get('chat_model') or (POLZA_MODEL if _provider(settings) == 'polza.ai' else DEFAULT_MODEL),
        'messages': [
            {'role': 'system', 'content': system_prompt(lang)},
            {'role': 'user', 'content': ('Скажи что-нибудь смешное и короткое про время '
                                         f'{hour}:00, одно-два предложения, по-русски, без эмодзи и списков.'
                                         if str(lang) == 'ru' else
                                         f'Say something short and funny about {hour}:00, one or two sentences, in English, no emoji, no lists.')}
        ],
        'stream': False,
        'options': {'num_predict': 120} if _provider(settings) != 'polza.ai' else {'max_tokens': 120}
    }).encode()
    try:
        if _provider(settings) == 'polza.ai':
            headers = {'Content-Type': 'application/json',
                       'Authorization': 'Bearer ' + str(settings.get('polza_api_key') or '')}
            req = urllib.request.Request(_url(settings) + '/chat/completions', data=body,
                                         headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode())
            msg = (data.get('choices') or [{}])[0].get('message') or {}
            return ' '.join(str(msg.get('content') or '').split())
        req = urllib.request.Request(_url(settings) + '/api/chat', data=body,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
    except (urllib.error.URLError, OSError, ValueError):
        return ''
    return ' '.join(str((data.get('message') or {}).get('content') or '').split())


def _degrees_ru(n):
    """градус / градуса / градусов, по числу, что стоит перед словом"""
    try:
        n = abs(int(float(str(n).replace(',', '.'))))
    except ValueError:
        return 'градусов'
    if n % 10 == 1 and n % 100 != 11:
        return 'градус'
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return 'градуса'
    return 'градусов'


# the temperatures of a weather line: «+8/+14°» (the pairs of the week), «+5°C», a bare «+12»
TEMP_RE = re.compile(r'([+-]?\d+)\s*/\s*([+-]?\d+)\s*°(?:C|F)?'
                     r'|([+-]?\d+)\s*°(?:C|F)?'
                     r'|([+-]?\d+)')


def weather_speak(text, lang='en'):
    """the weather line as her ears should hear it: «+5°C» is «плюс 5 градусов»,
    «+8/+14°» is «от плюс 8 до плюс 14 градусов» (English keeps it shorter:
    'minus 3 degrees', '8 to 14 degrees'); the bubble keeps its compact signs"""
    ru = str(lang) == 'ru'
    sign = {'+': 'плюс ' if ru else '', '-': 'минус ' if ru else 'minus '}

    def word(g):
        """the sign as a word, the number without its own sign"""
        s = g[:1]
        return sign.get(s, ''), g[1:] if s in '+-' else g

    out, at = [], 0
    t = str(text or '')
    for m in TEMP_RE.finditer(t):
        out.append(t[at:m.start()])
        if m.group(1) is not None:              # the pair «+8/+14°»
            sa, na = word(m.group(1))
            sb, nb = word(m.group(2))
            out.append(f'от {sa}{na} до {sb}{nb} {_degrees_ru(m.group(2))}' if ru
                       else f'{sa}{na} to {sb}{nb} degrees')
        elif m.group(3) is not None:            # the single «+5°C»
            s, n = word(m.group(3))
            out.append(f'{s}{n} {_degrees_ru(m.group(3))}' if ru else f'{s}{n} degrees')
        else:                                   # the bare «+12» of «от +12 до +17°C»
            s, n = word(m.group(4))
            out.append(s + n)
        at = m.end()
    out.append(t[at:])
    return ''.join(out)


def _geo(where, timeout=8):
    """(lat, lon, name) of the place, from the same wttr.in lookup the one-line report
    uses ('' looks wherever she is); None when the net is out or the place is unknown"""
    loc = urllib.parse.quote(str(where or '').strip(), safe=',%')
    url = 'https://wttr.in/{}?format=j1'.format(loc or '~')
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = json.loads(r.read().decode(errors='replace'))
        area = (data.get('nearest_area') or [{}])[0]
        lat, lon = float(area.get('latitude')), float(area.get('longitude'))
        name = ((area.get('areaName') or [{}])[0].get('value') or '').strip()
        return lat, lon, name
    except (urllib.error.URLError, OSError, ValueError, TypeError):
        return None


def _daily(lat, lon, days, timeout=8):
    """daily rows [{'date', 'code', 'min', 'max'}] for `days` days from Open-Meteo;
    [] when the net is out — she says so instead"""
    url = ('https://api.open-meteo.com/v1/forecast?latitude={}&longitude={}'
           '&daily=weather_code,temperature_2m_max,temperature_2m_min'
           '&timezone=auto&forecast_days={}').format(lat, lon, days)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = json.loads(r.read().decode(errors='replace'))
        d = data['daily']
        return [{'date': d['time'][i], 'code': int(d['weather_code'][i]),
                 'min': float(d['temperature_2m_min'][i]),
                 'max': float(d['temperature_2m_max'][i])}
                for i in range(len(d.get('time') or []))]
    except (urllib.error.URLError, OSError, ValueError, TypeError, KeyError, IndexError):
        return []


def wmo_desc(code, lang='en'):
    """the sky in words, for the code Open-Meteo sends back"""
    return WMO.get(int(code or 0), WMO[3])[0 if str(lang) == 'ru' else 1]


def _day_name(date, lang='en'):
    """the short weekday of a 'YYYY-MM-DD' date: «пн» / 'Mon'"""
    try:
        wd = time.strptime(str(date), '%Y-%m-%d').tm_wday
    except ValueError:
        return ''
    return WEEKDAYS['ru' if str(lang) == 'ru' else 'en'][wd]


def _fmt_tomorrow(name, day, lang='en'):
    """«Завтра, Ялта: пасмурно, от +4 до +10°C»"""
    desc = wmo_desc(day.get('code'), lang)
    mn, mx = day.get('min'), day.get('max')
    if str(lang) == 'ru':
        return f'Завтра, {name}: {desc}, от {mn:+.0f} до {mx:+.0f}°C'
    return f'Tomorrow, {name}: {desc}, {mn:+.0f} to {mx:+.0f} °C'


def _fmt_week(name, rows, lang='en'):
    """«Ялта, на неделю: ср +4/+9°, чт +6/+11°, ...»"""
    days = ', '.join('{} {:+.0f}/{:+.0f}°'.format(_day_name(r.get('date'), lang),
                                                  r.get('min'), r.get('max')) for r in rows)
    if str(lang) == 'ru':
        return f'{name}, на неделю: {days}'
    return f'{name}, this week: {days}'


def weather_report(where, lang='en', when='today', timeout=8):
    """one line about the sky, with the place up front («Yalta: +4°C, Пасмурно») — today from
    wttr.in, tomorrow or the whole week from Open-Meteo («Завтра, Ялта: ...»);
    '' when the net is out — she says so instead"""
    when = when if when in ('tomorrow', 'week') else 'today'
    if when == 'today':
        loc = urllib.parse.quote(str(where or '').strip(), safe=',%')
        lang = 'ru' if str(lang) == 'ru' else 'en'
        fmt = urllib.parse.quote('%l: %t, %C')   # the place, the temperature, the clouds
        url = 'https://wttr.in/{}?format={}&lang={}'.format(loc or '~', fmt, lang)
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                text = r.read().decode(errors='replace')
        except (urllib.error.URLError, OSError, ValueError):
            return ''
        return ' '.join(text.split()).lstrip(':').strip()[:120]   # no name: no empty «:»
    geo = _geo(where, timeout)                  # the same lookup the today's report uses
    if not geo:
        return ''
    lat, lon, name = geo
    rows = _daily(lat, lon, 2 if when == 'tomorrow' else 7, timeout)
    if not rows:
        return ''
    name = name or str(where or '').strip()
    if when == 'tomorrow':
        if len(rows) < 2:
            return ''
        return _fmt_tomorrow(name, rows[1], lang)[:200]
    return _fmt_week(name, rows, lang)[:240]

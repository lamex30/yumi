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


def weather_report(where, lang='en', timeout=8):
    """one line from wttr.in («+4°C, Пасмурно»), the nearest great place when `where`
    is ''; '' when the net is out — she says so instead"""
    loc = urllib.parse.quote(str(where or '').strip(), safe=',%')
    lang = 'ru' if str(lang) == 'ru' else 'en'
    fmt = urllib.parse.quote('%t, %C')      # the temperature and the clouds, no emoji
    url = 'https://wttr.in/{}?format={}&lang={}'.format(loc or '~', fmt, lang)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            text = r.read().decode(errors='replace')
    except (urllib.error.URLError, OSError, ValueError):
        return ''
    return ' '.join(text.split())[:120]

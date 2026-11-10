"""Her chat: answers through a local Ollama (no GTK here).

She asks http://127.0.0.1:11434 (settings 'ollama_url') with the conversation so far
and answers with the model from settings 'chat_model'
(default lakomoor/vikhr-llama-3.2-1b-instruct:1b, changed in Settings → Her mind).
Everything stays on this machine: Ollama runs locally, the words go nowhere else.
If Ollama is not running or has no models, ask() returns '' and she says so.
"""
import json
import urllib.error
import urllib.request

DEFAULT_MODEL = 'lakomoor/vikhr-llama-3.2-1b-instruct:1b'
DEFAULT_URL = 'http://127.0.0.1:11434'


def _url(settings):
    return str(settings.get('ollama_url') or DEFAULT_URL).rstrip('/')


def _get(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def alive(settings, timeout=0.8):
    """is there an Ollama on the other end"""
    try:
        return isinstance(_get(_url(settings) + '/api/version', timeout), dict)
    except (OSError, ValueError):
        return False


def models(settings):
    """installed model names, sorted; [] when Ollama is not reachable"""
    try:
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


def ask(settings, history, timeout=90):
    """one reply for the conversation `history` ([{role, content}, ...], user last);
    the reply text, or '' if it did not work out"""
    body = json.dumps({'model': settings.get('chat_model') or DEFAULT_MODEL,
                       'messages': history, 'stream': False,
                       'options': {'num_predict': 220}}).encode()
    req = urllib.request.Request(_url(settings) + '/api/chat', data=body,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
    except (urllib.error.URLError, OSError, ValueError):
        return ''
    return ' '.join(str((data.get('message') or {}).get('content') or '').split())

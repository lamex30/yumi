"""Her real voice: a local Silero TTS behind a tiny HTTP daemon (no GTK here).

tools/tts_setup.sh puts a venv with torch into ~/.local/share/yumi/tts. The first time
she speaks for real, this starts yumi/tts_daemon.py with that Python and keeps it warm,
so the next lines come at once. Phrases are cached in ~/.cache/yumi/tts (the oldest are
thrown away past the limit); if anything is missing, speak() returns None and she falls
back to her 8-bit blips.
"""
import hashlib
import io
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path

from . import config

PY = Path(os.environ.get('YUMI_TTS_PY') or Path.home() / '.local/share/yumi/tts/bin/python')
DAEMON = Path(__file__).resolve().parent / 'tts_daemon.py'
DIR = config.CACHE_DIR / 'tts'
STATE = DIR / 'port.json'
CONF = Path(config.CONFIG_DIR) / 'tts.json'
LIMIT = 256 * 1024 * 1024          # the phrase cache stays under this
_sys_py_ok = None


def _ping(port, timeout=0.4):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/ping', timeout=timeout) as r:
            info = json.loads(r.read().decode())
            return info if info.get('ok') else None
    except (OSError, ValueError):
        return None


def _state():
    try:
        info = json.loads(STATE.read_text())
    except (OSError, ValueError):
        return None
    port = info.get('port')
    ping = _ping(port) if port else None
    if not ping:
        return None
    return {'port': port, 'profile': ping.get('profile') or info.get('profile'),
            'downloading': ping.get('downloading') or None}


def downloading():
    """what the TTS daemon fetches this second (a name like «silero ru (v4_ru, ~100 MB)»),
    '' when it runs but fetches nothing, None when it is not running at all"""
    st = _state()
    if not st:
        return None
    return st.get('downloading') or ''


def _python():
    """the venv's Python if it is there, else the system one if it can run the daemon"""
    global _sys_py_ok
    if PY.exists():
        return PY
    if _sys_py_ok is None:
        try:
            _sys_py_ok = subprocess.run(
                [sys.executable, '-c', 'import torch, omegaconf, numpy'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        except OSError:
            _sys_py_ok = False
    return sys.executable if _sys_py_ok else None


_proc = None          # the daemon this Yumi started (an adopted one is stopped by its pid)
_shutting_down = False # close() already said goodbye: no thread may start the daemon again


def close():
    """stop the TTS daemon — Yumi's own child or an adopted one — and forbid new
    starts; safe to call more than once"""
    global _shutting_down
    _shutting_down = True
    _stop_daemon()


def _stop_daemon():
    """terminate the running daemon (ours or an adopted one); no flag is touched"""
    global _proc
    p, _proc = _proc, None
    if p:
        try:
            p.terminate()               # our own child: the direct handle is the surest way
        except (OSError, AttributeError):
            pass
    pid = _file_pid()                   # the child died at once — or it was an adopted one
    if pid and p is None:
        try:
            os.kill(pid, signal.SIGTERM)
        except (OSError, TypeError):
            pass
    for _ in range(10):                 # 2 s: SIGTERM ends it at once; SIGKILL is for the stuck
        if pid is None or not _is_daemon(pid):
            return
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except (OSError, TypeError):
        pass


# ---- the voice profile: settings.json and tts.json stay one thing ----

def _profile_defaults():
    return {name: config.TTS_PROFILE_DEFAULTS[key]
            for key, name in config.TTS_PROFILE_KEYS.items()}


def profile_from_settings(settings):
    """the tts.json-shaped profile the app's settings ask for"""
    out = _profile_defaults()
    for key, name in config.TTS_PROFILE_KEYS.items():
        value = settings.get(key, out.get(name))
        if name == 'pitch':
            try:
                value = round(max(0.5, min(2.0, float(value))), 3)
            except (TypeError, ValueError):
                value = out[name]
        elif value not in (None, ''):
            value = str(value)
        else:
            value = out[name]
        out[name] = value
    return out


def current_profile():
    """what tts_daemon would load now (tts.json over its own defaults)"""
    data = _profile_defaults()
    try:
        saved = json.loads(CONF.read_text())
        if isinstance(saved, dict):
            for name in data:
                if name in saved and saved[name] is not None:
                    data[name] = saved[name]
    except (OSError, ValueError):
        pass
    return data


def write_profile(profile):
    """save the profile where the daemon reads it; unknown manual keys are kept"""
    data = {}
    try:
        saved = json.loads(CONF.read_text())
        if isinstance(saved, dict):
            data.update(saved)
    except (OSError, ValueError):
        pass
    data.update(profile)
    try:
        CONF.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONF.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + '\n')
        tmp.replace(CONF)
        return True
    except OSError:
        return False


def restart():
    """stop the daemon so the next speak() starts one with the new profile;
    unlike close() this leaves the door open for that next start"""
    global _shutting_down
    _shutting_down = True
    _stop_daemon()
    _shutting_down = False
    return True


def sync_profile(settings):
    """make tts.json match the settings and restart the daemon only when the
    profile really changed (a plain settings save must not relaunch the voice)"""
    want = profile_from_settings(settings)
    have = current_profile()
    if want == have:
        return {'changed': False, 'profile': have}
    if not write_profile(want):
        return {'changed': False, 'profile': have, 'error': 'tts.json is not writable'}
    restart()
    return {'changed': True, 'profile': want}


def _is_daemon(pid):
    """true when this pid really is our daemon's process: a recycled pid must never be signalled"""
    try:
        with open(f'/proc/{pid}/cmdline', 'rb') as f:
            return DAEMON.name in f.read().decode(errors='replace')
    except OSError:
        return False


def _file_pid():
    """the running daemon's pid: the port file's word first, then a scan of /proc
    (an old port file has no pid at all, yet the daemon may still be there)"""
    try:
        pid = json.loads(STATE.read_text()).get('pid')
    except (OSError, ValueError, AttributeError):
        pid = None
    if isinstance(pid, int) and _is_daemon(pid):
        return pid
    for name in os.listdir('/proc'):
        if name.isdigit() and _is_daemon(int(name)):
            return int(name)
    return None


def _ensure():
    """the running daemon's port and profile, starting it if needed; None if it cannot be"""
    if _shutting_down:                  # she is leaving: no new daemon rises behind her
        return None
    DIR.mkdir(parents=True, exist_ok=True)
    info = _state()
    if info:
        return info
    py = _python()
    if not py:
        return None
    global _proc
    try:
        _proc = subprocess.Popen([str(py), str(DAEMON)], start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        _proc = None
        return None
    if _shutting_down:                  # close() ran while the daemon was being born
        try:
            _proc.terminate()
        except OSError:
            pass
        return None
    for _ in range(100):               # 20 s: importing torch is the slow part
        info = _state()
        if info:
            return info
        time.sleep(0.2)
    return None


def _lang_runs(text):
    """the line grouped by script: «Красноярск: плюс 7°, light rain» becomes a
    Russian run and an English one — each half gets the voice that can say it"""
    words = text.split()
    kinds = [next((('ru' if '\u0400' <= c <= '\u04ff' else 'en') for c in w if c.isalpha()),
                  None) for w in words]
    for i, k in enumerate(kinds):      # a word without letters (a number) follows its neighbours
        if k is None:
            kinds[i] = next((x for x in kinds[i + 1:] if x),
                            next((x for x in reversed(kinds[:i]) if x), 'ru'))
    runs, cur, cur_k = [], [], None
    for w, k in zip(words, kinds):
        if cur and k != cur_k:
            runs.append((cur_k, ' '.join(cur)))
            cur = []
        cur_k = k
        cur.append(w)
    if cur:
        runs.append((cur_k, ' '.join(cur)))
    return runs


def _ask(info, chunk, volume, lang):
    """one chunk to the daemon: the WAV bytes, or None when it did not work out"""
    url = f"http://127.0.0.1:{info['port']}/tts?" + urllib.parse.urlencode(
        {'text': chunk, 'vol': round(volume, 2), 'lang': lang})
    try:
        with urllib.request.urlopen(url, timeout=300) as r:
            data = r.read()
    except (urllib.error.URLError, OSError):
        return None
    return data if data.startswith(b'RIFF') else None


def _concat(parts):
    """one WAV out of many ('wav' parts are opened, 'silence' parts are raw frames;
    they are all 16-bit mono at the same rate)"""
    frames, fmt = [], None
    for kind, data in parts:
        if kind == 'silence':
            frames.append(data)
            continue
        with wave.open(io.BytesIO(data)) as r:
            here = (r.getnchannels(), r.getsampwidth(), r.getframerate())
            if fmt and here != fmt:
                raise wave.Error('the voices disagree on the sound format')
            fmt = fmt or here
            frames.append(r.readframes(r.getnframes()))
    out = io.BytesIO()
    with wave.open(out, 'wb') as w:
        w.setnchannels(fmt[0])
        w.setsampwidth(fmt[1])
        w.setframerate(fmt[2])
        for f in frames:
            w.writeframes(f)
    return out.getvalue()


def speak(text, volume=0.5):
    """voice the line: the path of a WAV, or None (she falls back to her blips);
    a line of two languages is said by both voices, a small pause in between"""
    text = ' '.join(str(text).split())[:500]
    # convert digits like 14:00 to words for better Silero TTS pronunciation
    text = _convert_digits_for_tts(text)
    text = apply_stress(text)      # the user's stress marks win over the auto-accentor
    if not text:
        return None
    info = _ensure()
    if not info:
        return None
    key = hashlib.sha1(f"{info['profile']}|{volume:.2f}|{text}".encode()).hexdigest()[:20]
    path = DIR / f'{key}.wav'
    if not path.exists():
        parts, done = [], False
        for i, (lang, chunk) in enumerate(_lang_runs(text)):
            data = _ask(info, chunk, volume, lang)
            if data:
                if parts:
                    parts.append(('silence', b'\x00\x00' * 3600))   # a quarter of a second
                parts.append(('wav', data))
                done = True
        if not done:
            return None
        try:
            tmp = path.with_suffix('.part')
            tmp.write_bytes(_concat(parts) if len(parts) > 1 else parts[0][1])
            tmp.replace(path)
        except (OSError, wave.Error):
            return None
        _evict()
    return path


# ---- digits into words: Silero reads «14:00» badly, «четырнадцать часов» well ----

_digit_to_word = {
    '0': 'ноль', '1': 'один', '2': 'два', '3': 'три', '4': 'четыре',
    '5': 'пять', '6': 'шесть', '7': 'семь', '8': 'восемь', '9': 'девять',
}
_RU_SMALL = ['ноль', 'один', 'два', 'три', 'четыре', 'пять', 'шесть', 'семь', 'восемь',
             'девять', 'десять', 'одиннадцать', 'двенадцать', 'тринадцать', 'четырнадцать',
             'пятнадцать', 'шестнадцать', 'семнадцать', 'восемнадцать', 'девятнадцать']
_RU_TENS = ['', '', 'двадцать', 'тридцать', 'сорок', 'пятьдесят', 'шестьдесят',
            'семьдесят', 'восемьдесят', 'девяносто']
_RU_HUNDREDS = ['', 'сто', 'двести', 'триста', 'четыреста', 'пятьсот', 'шестьсот',
                'семьсот', 'восемьсот', 'девятьсот']
_EN_SMALL = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine',
             'ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen',
             'seventeen', 'eighteen', 'nineteen']
_EN_TENS = ['', '', 'twenty', 'thirty', 'forty', 'fifty',
            'sixty', 'seventy', 'eighty', 'ninety']
_TIME_RE = re.compile(r'(?<!\d)(\d{1,2}):(\d{2})(?!\d)(\s*(?:am|pm)\b)?', re.IGNORECASE)


def _ru_plural(n, one, few, many):
    """the Russian form of a noun after the number n: 1 час, 2 часа, 5 часов"""
    n %= 100
    if 11 <= n <= 19:
        return many
    d = n % 10
    return one if d == 1 else few if 2 <= d <= 4 else many


def _ru_words(n, female=False):
    """a number as Russian words, 0..9999; bigger ones are spelled digit by digit;
    `female` for nouns like «минута»: двадцать одна, а не двадцать один"""
    if n > 9999 or n < 0:
        return ' '.join(_digit_to_word[d] for d in str(n))
    one, two = ('одна', 'две') if female else (_RU_SMALL[1], _RU_SMALL[2])
    parts = []
    th, rest = divmod(n, 1000)
    if th:
        word = 'одна' if th == 1 else 'две' if th == 2 else _ru_words(th)
        parts.append(word + ' ' + _ru_plural(th, 'тысяча', 'тысячи', 'тысяч'))
    h, rest = divmod(rest, 100)
    if h:
        parts.append(_RU_HUNDREDS[h])
    t, u = divmod(rest, 10)
    if t == 1:
        parts.append(_RU_SMALL[10 + u])
    else:
        if t >= 2:
            parts.append(_RU_TENS[t])
        if u or (not t and not h and not th):
            parts.append(one if u == 1 else two if u == 2 else _RU_SMALL[u])
    return ' '.join(parts)


def _en_words(n):
    """a number as English words, 0..99 (enough for the clock and the temperatures)"""
    if n < 20:
        return _EN_SMALL[n]
    t, u = divmod(n, 10)
    return _EN_TENS[t] + (' ' + _EN_SMALL[u] if u else '')


def _en_number(n):
    """a bigger number as English words, 0..9999; bigger ones are spelled digit by digit"""
    n = int(n)
    if n > 9999 or n < 0:
        return ' '.join(_EN_SMALL[int(d)] for d in str(abs(n))) if n else _EN_SMALL[0]
    parts = []
    for div, word in ((1000, 'thousand'), (100, 'hundred')):
        q, n = divmod(n, div)
        if q:
            parts.append(_en_words(q) + ' ' + word)
    if n or not parts:
        parts.append(_en_words(n))
    return ' '.join(parts)


def _is_ru(text):
    """the line is Russian when it holds any Cyrillic at all: a stray English word
    does not change the language of her numbers (and vice versa)"""
    return any('\u0400' <= c <= '\u04ff' for c in text)


def _convert_digits_for_tts(text):
    """«Сейчас 14:05» becomes «Сейчас четырнадцать часов пять минут», «2:14 pm»
    becomes «two fourteen p m»; other numbers become words in the line's language"""
    ru = _is_ru(text)

    def time_repl(m):
        h, mins, ap = int(m.group(1)), int(m.group(2)), (m.group(3) or '').lower().strip()
        if ap or not ru:            # her sleepy English clock: «two fourteen p m»
            words = _en_words(h)
            if mins:
                words += (' oh ' + _EN_SMALL[mins]) if mins < 10 else ' ' + _en_words(mins)
            if ap:
                words += ' p m' if ap == 'pm' else ' a m'
            return words
        words = _ru_words(h) + ' ' + _ru_plural(h, 'час', 'часа', 'часов')
        if mins:
            words += ' ' + _ru_words(mins, female=True) + ' ' + _ru_plural(mins, 'минута', 'минуты', 'минут')
        return words

    def num_repl(m):
        return _ru_words(int(m.group())) if ru else _en_number(int(m.group()))

    text = _TIME_RE.sub(time_repl, text)
    return re.sub(r'\d+', num_repl, text)

# ---- stresses: her own dictionary where the auto-accentor gets it wrong ----

STRESS = config.CONFIG_DIR / 'stress.json'   # {'торты': 'т+орты'}; + marks the stressed vowel
STARTER_STRESS = {                 # the words Silero's accentor keeps getting wrong
    'торты': 'т+орты', 'тортов': 'т+ортов', 'тортами': 'т+ортами',
    'звонит': 'звон+ит', 'позвонит': 'позвон+ит', 'позвонишь': 'позвон+ишь',
    'договор': 'догов+ор', 'договора': 'догов+ора',
    'каталог': 'катал+ог', 'квартал': 'кварт+ал', 'аппарат': 'аппар+ат',
}
_stress_cache = None               # (the file's mtime, {'word': 'stressed'})


def stress_map():
    """the stress dictionary, read anew whenever the file changes (so edits land at once)"""
    global _stress_cache
    try:
        mtime = STRESS.stat().st_mtime
    except OSError:
        try:                       # no file yet: her starter set, saved for editing
            STRESS.parent.mkdir(parents=True, exist_ok=True)
            STRESS.write_text(json.dumps(STARTER_STRESS, ensure_ascii=False, indent=4) + '\n')
        except OSError:
            return {}
        return dict(STARTER_STRESS)
    if _stress_cache and _stress_cache[0] == mtime:
        return _stress_cache[1]
    try:
        data = json.loads(STRESS.read_text(encoding='utf-8'))
        words = {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)
                 and k.strip() and v.strip()}
    except (OSError, ValueError):
        words = {}
    words = {k: v for k, v in words.items() if not k.startswith('_')}
    _stress_cache = (mtime, words)
    return words


def apply_stress(text):
    """the user's stress marks into the line: «торты» becomes «т+орты»; only whole
    words are touched (a «+» inside them survives the model's own accentor)"""
    words = stress_map()
    if not words or not any('\u0400' <= c <= '\u04ff' for c in text):
        return text
    for word, stressed in sorted(words.items(), key=lambda kv: -len(kv[0])):
        text = re.sub(r'(?<![\u0400-\u04ff])' + re.escape(word) + r'(?![\u0400-\u04ff])',
                      stressed, text, flags=re.IGNORECASE)
    return text


def _evict():
    try:
        wavs = sorted(DIR.glob('*.wav'), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in wavs)
        for p in wavs:
            if total <= LIMIT:
                break
            total -= p.stat().st_size
            p.unlink()
    except OSError:
        pass


def wav_len(path):
    """the file's length in seconds, or None if it cannot be read"""
    try:
        with wave.open(str(path)) as w:
            return w.getnframes() / max(1, w.getframerate())
    except (OSError, wave.Error):
        return None

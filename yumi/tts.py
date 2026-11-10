"""Her real voice: a local Silero TTS behind a tiny HTTP daemon (no GTK here).

tools/tts_setup.sh puts a venv with torch into ~/.local/share/yumi/tts. The first time
she speaks for real, this starts yumi/tts_daemon.py with that Python and keeps it warm,
so the next lines come at once. Phrases are cached in ~/.cache/yumi/tts (the oldest are
thrown away past the limit); if anything is missing, speak() returns None and she falls
back to her 8-bit blips.
"""
import hashlib
import json
import os
import re
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
    return {'port': port, 'profile': ping.get('profile') or info.get('profile')}


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


def _ensure():
    """the running daemon's port and profile, starting it if needed; None if it cannot be"""
    DIR.mkdir(parents=True, exist_ok=True)
    info = _state()
    if info:
        return info
    py = _python()
    if not py:
        return None
    try:
        subprocess.Popen([str(py), str(DAEMON)], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return None
    for _ in range(100):               # 20 s: importing torch is the slow part
        info = _state()
        if info:
            return info
        time.sleep(0.2)
    return None


def speak(text, volume=0.5):
    """voice the line: the path of a WAV, or None (she falls back to her blips)"""
    text = ' '.join(str(text).split())[:500]
    # convert digits like 14:00 to words for better Silero TTS pronunciation
    text = _convert_digits_for_tts(text)
    if not text:
        return None
    info = _ensure()
    if not info:
        return None
    key = hashlib.sha1(f"{info['profile']}|{volume:.2f}|{text}".encode()).hexdigest()[:20]
    path = DIR / f'{key}.wav'
    if not path.exists():
        url = f"http://127.0.0.1:{info['port']}/tts?" + urllib.parse.urlencode(
            {'text': text, 'vol': round(volume, 2)})
        try:
            with urllib.request.urlopen(url, timeout=300) as r:
                data = r.read()
        except (urllib.error.URLError, OSError):
            return None
        if not data.startswith(b'RIFF'):
            return None
        try:
            tmp = path.with_suffix('.part')
            tmp.write_bytes(data)
            tmp.replace(path)
        except OSError:
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
_EN_TENS = ['', '', 'twenty', 'thirty', 'forty', 'fifty']
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
    """a number as English words, 0..59 (enough for the clock)"""
    if n < 20:
        return _EN_SMALL[n]
    t, u = divmod(n, 10)
    return _EN_TENS[t] + (' ' + _EN_SMALL[u] if u else '')


def _convert_digits_for_tts(text):
    """«Сейчас 14:05» becomes «Сейчас четырнадцать часов пять минут», «2:14 pm»
    becomes «two fourteen p m»; other numbers become Russian words"""
    def time_repl(m):
        h, mins, ap = int(m.group(1)), int(m.group(2)), (m.group(3) or '').lower().strip()
        if ap:                      # her sleepy English clock: «two fourteen p m»
            words = _en_words(h)
            if mins:
                words += (' oh ' + _EN_SMALL[mins]) if mins < 10 else ' ' + _en_words(mins)
            return words + (' p m' if ap == 'pm' else ' a m')
        words = _ru_words(h) + ' ' + _ru_plural(h, 'час', 'часа', 'часов')
        if mins:
            words += ' ' + _ru_words(mins, female=True) + ' ' + _ru_plural(mins, 'минута', 'минуты', 'минут')
        return words

    def num_repl(m):
        return _ru_words(int(m.group()))

    text = _TIME_RE.sub(time_repl, text)
    return re.sub(r'\d+', num_repl, text)

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

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

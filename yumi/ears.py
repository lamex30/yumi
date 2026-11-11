"""Her ears: a local Vosk hears the microphone (no GTK here).

tools/stt_setup.sh puts a venv with vosk into ~/.local/share/yumi/stt. The first time
she listens, this starts yumi/stt_daemon.py with that Python and keeps it warm; the small
Russian model (~45 MB) is downloaded once on the first listen (the settings can ask for the
big one, ~1.8 GB, much sharper). listen() returns the heard
text, '' when there was silence, and None when the ears cannot work at all (no vosk, no
microphone tool).
"""
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import config
from .stt_daemon import VERSION      # a running daemon older than this gets restarted

PY = Path(os.environ.get('YUMI_STT_PY') or Path.home() / '.local/share/yumi/stt/bin/python')
DAEMON = Path(__file__).resolve().parent / 'stt_daemon.py'
DIR = config.CACHE_DIR / 'stt'
STATE = DIR / 'port.json'
LOG = config.STATE_DIR / 'stt.log'      # what the daemon printed, for the troubleshooting
_sys_py_ok = None


def _ping(port, timeout=0.4):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/ping', timeout=timeout) as r:
            info = json.loads(r.read().decode())
            return info if info.get('ok') else None
    except (OSError, ValueError):
        return None


def _state():
    """the running daemon's info, or None; a stale one (older code) is killed here"""
    try:
        info = json.loads(STATE.read_text())
    except (OSError, ValueError):
        return None
    port = info.get('port')
    ping = _ping(port) if port else None
    if not ping:
        return None
    if ping.get('version') != VERSION:  # an older twin: its code predates this ears.py
        try:
            os.kill(info.get('pid'), signal.SIGTERM)
        except (OSError, TypeError, ProcessLookupError):
            pass
        try:
            STATE.unlink()
        except OSError:
            pass
        return None
    return {'port': port}


def _python():
    """the venv's Python if it is there, else the system one if it can run the daemon"""
    global _sys_py_ok
    if PY.exists():
        return PY
    if _sys_py_ok is None:
        try:
            _sys_py_ok = subprocess.run(
                [sys.executable, '-c', 'import vosk'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        except OSError:
            _sys_py_ok = False
    return sys.executable if _sys_py_ok else None


def _ensure():
    """the running daemon's port, starting it if needed; None if it cannot be"""
    DIR.mkdir(parents=True, exist_ok=True)
    info = _state()
    if info:
        return info
    py = _python()
    if not py:
        return None
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        log = open(LOG, 'ab')           # the daemon's prints live here, for the troubleshooting
        subprocess.Popen([str(py), str(DAEMON)], start_new_session=True,
                         stdout=log, stderr=log)
    except OSError:
        return None
    for _ in range(50):                 # 10 s: importing vosk is quick, the first model download is not
        info = _state()
        if info:
            return info
        time.sleep(0.2)
    return None


def mics():
    """the microphones the ears can hear: [{'name': ..., 'label': ...}], [] when none or no ears"""
    info = _ensure()
    if not info:
        return []
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{info['port']}/mics", timeout=5) as r:
            data = json.loads(r.read().decode())
    except (urllib.error.URLError, OSError, ValueError):
        return []
    return data.get('mics') if isinstance(data.get('mics'), list) else []


def listen(lang='ru', secs=7, size='small', mic=None):
    """hear one phrase: the text, '' for silence, None when the ears are not set up"""
    info = _ensure()
    if not info:
        return None
    q = {'lang': lang, 'secs': round(secs, 1), 'size': size}
    if mic:
        q['mic'] = mic
    url = f"http://127.0.0.1:{info['port']}/listen?" + urllib.parse.urlencode(q)
    try:
        # the first listen may download the model (~45 MB), hence the generous timeout
        with urllib.request.urlopen(url, timeout=max(300, secs + 10)) as r:
            data = json.loads(r.read().decode())
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return data.get('text') if isinstance(data.get('text'), str) else ''


def wake(lang='ru', word='юми', secs=20, size='small', mic=None):
    """wait for the wake word (say «Юми» and she is all ears): a dict
    {'woke': bool, 'text': what came after the word, '' when it was said alone},
    or None when the ears cannot work at all. A manual listen takes the mic over."""
    info = _ensure()
    if not info:
        return None
    q = {'lang': lang, 'word': word, 'secs': round(secs, 1), 'size': size}
    if mic:
        q['mic'] = mic
    url = f"http://127.0.0.1:{info['port']}/wake?" + urllib.parse.urlencode(q)
    try:
        with urllib.request.urlopen(url, timeout=max(300, secs + 10)) as r:
            data = json.loads(r.read().decode())
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return {'woke': bool(data.get('woke')), 'text': str(data.get('text') or '')}


def tap(on, lang='ru', size='small', mic=None):
    """the ears' diary: everything the microphone hears goes to /tmp/yumi — the sound
    into ears-*.wav and the heard words into recogn.txt («time - phrase»), in the
    daemon's own thread; True/False when the daemon answered, None when it is away"""
    info = _ensure() if on else _state()    # off needs no daemon: the wish is on disk
    if not info:
        if on:
            return None
        try:
            DIR.mkdir(parents=True, exist_ok=True)
            (DIR / 'tap.json').write_text(json.dumps({'on': False, 'want': {}}))
        except OSError:
            pass
        return False
    q = {'on': 1 if on else 0, 'lang': lang, 'size': size}
    if mic:
        q['mic'] = mic
    url = f"http://127.0.0.1:{info['port']}/tap?" + urllib.parse.urlencode(q)
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read().decode())
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return bool(data.get('on'))


def stop():
    """one more click on her mic button: she stops waiting for the phrase"""
    info = _state()
    if info:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{info['port']}/stop", timeout=1)
        except (urllib.error.URLError, OSError):
            pass

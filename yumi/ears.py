"""Her ears: local faster-whisper reads the phrases, local Vosk hears their ends (no GTK here).

tools/stt_setup.sh puts a venv with vosk and faster-whisper into ~/.local/share/yumi/stt. The
first time she listens, this starts yumi/stt_daemon.py with that Python and keeps it warm; the
small Russian vosk model (~45 MB) and the small whisper model (~460 MB) are downloaded once on
the first listens. listen() returns the heard
text, '' when there was silence, and None when the ears cannot work at all (no vosk, no
microphone tool).
"""
import json
import os
import signal
import subprocess
import sys
import threading
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
_spawn_lock = threading.Lock()          # ears start from several threads: one daemon at a time
_proc = None          # the daemon Yumi started (not one already running)


def close():
    """stop the STT daemon Yumi started, if it is still ours; safe to call more than once"""
    global _proc
    p = _proc
    _proc = None
    if p:
        try:
            p.terminate()
        except (OSError, AttributeError):
            pass


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
    if ping.get('pid') and ping.get('pid') != info.get('pid'):
        # the state file lied about the pid (an old adoption used to write the
        # wrong one, and a false pid made the stale twin unkillable): fix it here
        try:
            STATE.write_text(json.dumps({'port': port, 'pid': ping['pid'],
                                         'version': ping.get('version')}))
        except OSError:
            pass
    if ping.get('version') != VERSION:  # an older twin: its code predates this ears.py
        pid = ping.get('pid') or info.get('pid')
        try:
            os.kill(pid, signal.SIGTERM)
        except (OSError, TypeError, ProcessLookupError):
            pass
        for _ in range(15):             # 3 s: it must be dead and off the port before a
            if not _ping(port):         # fresh one starts, or the two will fight over
                break                   # the port and the loser ends up on a random one
            time.sleep(0.2)
        else:
            try:                        # stuck in teardown: no more mr. nice signal
                os.kill(pid, signal.SIGKILL)
            except (OSError, TypeError, ProcessLookupError):
                pass
            time.sleep(0.5)
        try:
            STATE.unlink()
        except OSError:
            pass
        return None
    return {'port': port, 'downloading': ping.get('downloading') or None}


def downloading():
    """what the ears daemon fetches this second (a name like «vosk ru (~45 MB)»),
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
    with _spawn_lock:           # a race here spawns twin daemons: two vosks, two recorders
        info = _state()
        if info:
            return info         # the other thread won the start: its daemon is good enough
        py = _python()
        if not py:
            return None
        try:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            log = open(LOG, 'ab')           # the daemon's prints live here, for the troubleshooting
            _proc = subprocess.Popen([str(py), str(DAEMON)], start_new_session=True,
                                     stdout=log, stderr=log)
        except OSError:
            _proc = None
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


def listen(lang='ru', secs=7, size='small', mic=None, ear=None, engine=None):
    """hear one phrase: {'text': the words whisper read, 'vosk': the words vosk read}
    ('' for silence, None when the ears are not set up); the mind tries both readings"""
    info = _ensure()
    if not info:
        return None
    q = {'lang': lang, 'secs': round(secs, 1), 'size': size}
    if engine and engine != 'whisper':
        q['engine'] = engine
    if mic:
        q['mic'] = mic
    if ear:
        q['ear'] = ear
    url = f"http://127.0.0.1:{info['port']}/listen?" + urllib.parse.urlencode(q)
    try:
        # the first listen may download the model (~45 MB), hence the generous timeout
        with urllib.request.urlopen(url, timeout=max(300, secs + 10)) as r:
            data = json.loads(r.read().decode())
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if 'error' in data:
        return None
    return {'text': str(data.get('text') or ''), 'vosk': str(data.get('vosk') or '')}


def wake(lang='ru', word='юми', secs=20, size='small', mic=None, ear=None,
         sensitivity='normal', confirm=True, model_size=None, judge_log=False, engine=None):
    """wait for the wake word (say «Юми» and she is all ears): a dict
    {'woke': bool, 'text': what came after the word, '' when it was said alone},
    or None when the ears cannot work at all. A manual listen takes the mic over."""
    info = _ensure()
    if not info:
        return None
    q = {'lang': lang, 'word': word, 'secs': round(secs, 1), 'size': size,
         'sensitivity': sensitivity, 'confirm': '1' if confirm else '0',
         'judge_log': '1' if judge_log else '0'}
    if engine and engine != 'whisper':
        q['engine'] = engine
    if model_size:
        q['model_size'] = model_size
    if mic:
        q['mic'] = mic
    if ear:
        q['ear'] = ear
    url = f"http://127.0.0.1:{info['port']}/wake?" + urllib.parse.urlencode(q)
    try:
        with urllib.request.urlopen(url, timeout=max(300, secs + 10)) as r:
            data = json.loads(r.read().decode())
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return {'woke': bool(data.get('woke')), 'text': str(data.get('text') or ''),
            'stop': bool(data.get('stop'))}


def tap(on, lang='ru', size='small', mic=None, ear=None):
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
    if ear:
        q['ear'] = ear
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


def mute(secs=6.0):
    """the ears close for this many seconds: her own voice is playing, so she must
    not hear herself (no canceller needed); best effort — the ears being away is fine"""
    info = _state()
    if not info:
        return
    try:
        urllib.request.urlopen(
            f"http://127.0.0.1:{info['port']}/mute?secs={round(float(secs), 1)}", timeout=1)
    except (urllib.error.URLError, OSError):
        pass

"""Music player integration for Yumi.

Features:
- MPD (Music Player Daemon) integration via mpc command
- playerctl for MPRIS-compatible players (vlc, spotify, brave, etc.)
- "СТОП" (STOP) keyword to stop all playback
- Music library in the user's music folder (~/Музыка, resolved through xdg-user-dir)
- Hotkey support: next, previous, play/pause

Dependencies: mpc (for MPD), playerctl (for MPRIS players)
Optional: mpd for playing her own library
"""

import os
import random
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

ENV = dict(os.environ)
ENV.pop('WAYLAND_SOCKET', None)


def _xdg_music_dir():
    """the user's music folder: xdg-user-dir, then the env, user-dirs.dirs, then the usual names"""
    home = Path.home()
    try:
        d = subprocess.run(['xdg-user-dir', 'MUSIC'],
                           capture_output=True, text=True, timeout=3)
        if d.returncode == 0:
            p = Path(d.stdout.strip())
            if p.is_dir() and p != home:
                return p
    except (OSError, subprocess.SubprocessError):
        pass

    candidates = []
    if os.environ.get('XDG_MUSIC_DIR'):
        candidates.append(Path(os.environ['XDG_MUSIC_DIR']))
    cfg = Path(os.environ.get('XDG_CONFIG_HOME') or home / '.config') / 'user-dirs.dirs'
    try:
        for line in cfg.read_text(encoding='utf-8', errors='replace').splitlines():
            line = line.strip()
            if line.startswith('XDG_MUSIC_DIR'):
                value = line.split('=', 1)[1].strip().strip('"')
                candidates.append(Path(value.replace('$HOME', str(home)).replace('${HOME}', str(home))))
                break
    except OSError:
        pass
    candidates += [home / 'Музыка', home / 'Music']   # localized systems rarely export the env
    for p in candidates:
        p = Path(os.path.expandvars(str(p))) if '$' in str(p) else p
        if p.is_dir() and p != home:
            return p
    return home / 'Music'


# Music directory - the user's music folder itself
_MUSIC_DIR = _xdg_music_dir()

# Track running processes
_running_processes = {'mpd': None, 'player': None}
_stop_event = threading.Event()


def get_music_dir():
    """Return the Yumi music directory path."""
    return _MUSIC_DIR


def _mpc(*args, timeout=3):
    """one mpc command: a CompletedProcess, or None when mpc/mpd are away"""
    if not shutil.which('mpc'):
        return None
    try:
        return subprocess.run(['mpc', *args], capture_output=True, text=True,
                              timeout=timeout, env=ENV)
    except (OSError, subprocess.SubprocessError):
        return None


def _playerctl(*args, timeout=3):
    """one playerctl command: a CompletedProcess, or None when playerctl is away"""
    if not shutil.which('playerctl'):
        return None
    try:
        return subprocess.run(['playerctl', *args], capture_output=True, text=True,
                              timeout=timeout, env=ENV)
    except (OSError, subprocess.SubprocessError):
        return None


def get_mpd_status():
    """Get MPD status via mpc, or None when mpd does not answer."""
    r = _mpc('status')
    if r is not None and r.returncode == 0 and r.stdout.strip():
        return r.stdout
    return None


def is_mpd_playing():
    """Check if MPD is currently playing."""
    status = get_mpd_status()
    if status is None:
        return False
    return 'playing' in status.lower()


def _mpd_ready():
    return get_mpd_status() is not None


def _ensure_mpd():
    """start the user's mpd when it is installed but quiet; True when it answers"""
    if _mpd_ready() or not shutil.which('mpd'):
        return _mpd_ready()
    try:
        proc = subprocess.Popen(['mpd'], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, env=ENV,
                                start_new_session=True)
        _running_processes['mpd'] = proc.pid
    except (OSError, subprocess.SubprocessError):
        pass
    time.sleep(0.4)                    # the daemon takes a moment to open its port
    return _mpd_ready()


def _players():
    """names of the MPRIS players around, or '' when playerctl is away"""
    r = _playerctl('-l')
    return r.stdout.strip() if r is not None and r.returncode == 0 else ''


def _player_status():
    """the active player's state: 'Playing', 'Paused', 'Stopped', or ''"""
    r = _playerctl('status')
    return r.stdout.strip() if r is not None and r.returncode == 0 else ''


def play_music(file_path=None, directory=None):
    """Play music from her library using an available player.

    Args:
        file_path: Specific file to play, or None for the whole library
        directory: Directory to play from, defaults to the music dir

    Returns:
        True if playback started, False otherwise
    """
    # Stop any existing playback first
    stop_music(force=True)

    directory = Path(directory) if directory else _MUSIC_DIR
    if not directory.is_dir():
        return False

    _stop_event.clear()

    # mpd: load the library and play (mpc over TCP accepts only paths inside mpd's
    # music_directory, so the whole library root is the reliable way in)
    if _ensure_mpd():
        _mpc('clear')
        _mpc('add', str(directory))
        if not (_mpc('playlist').stdout or '').strip():
            _mpc('add', '/')
        _mpc('shuffle')                # a fresh deal of the queue: it never starts
                                       # with the same first song twice in a row
        _mpc('play')
        if is_mpd_playing():
            return True

    # fallback: ffplay over the first audio files of the directory (she picks one)
    if shutil.which('ffplay'):
        try:
            songs = sorted(p for p in directory.iterdir()
                           if p.suffix.lower() in ('.mp3', '.flac', '.ogg', '.opus', '.wav', '.m4a'))
        except OSError:
            songs = []
        if songs:
            song = file_path or random.choice(songs)   # a surprise first song, not the same one
            try:
                proc = subprocess.Popen(
                    ['ffplay', '-nodisp', '-autoexit', '-loglevel', 'quiet', str(song)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env=ENV, start_new_session=True
                )
                _running_processes['player'] = proc.pid
                return True
            except OSError:
                pass

    return False


def stop_music(force=False):
    """Stop all playback: MPRIS players, MPD, and any process we started.

    Args:
        force: also stop processes we spawned (ffplay leftovers)
    """
    stopped = False

    # MPRIS players (vlc, spotify, brave, ...)
    r = _playerctl('stop')
    if r is not None:
        stopped = True

    # MPD
    r = _mpc('stop')
    if r is not None:
        stopped = True

    # Processes we spawned ourselves
    if force:
        for name, pid in list(_running_processes.items()):
            if pid:
                try:
                    os.killpg(os.getpgid(pid), 15)
                except (OSError, ProcessLookupError):
                    try:
                        os.kill(pid, 15)
                    except (OSError, ProcessLookupError):
                        pass
            _running_processes[name] = None

    _stop_event.set()
    return stopped


def next_track():
    """Skip to the next track on the player that is actually playing."""
    if _player_status().lower().startswith(('playing', 'paused')):
        r = _playerctl('next')
        return r is not None and r.returncode == 0
    if _mpd_ready():
        r = _mpc('next')
        return r is not None and r.returncode == 0
    return False


def previous_track():
    """Go back to the previous track on the player that is actually playing."""
    if _player_status().lower().startswith(('playing', 'paused')):
        r = _playerctl('previous')
        return r is not None and r.returncode == 0
    if _mpd_ready():
        r = _mpc('prev')
        return r is not None and r.returncode == 0
    return False


def now_playing():
    """Who plays right now: (artist, title) of the track that has the sound,
    or None when the room is quiet or no names are to be had.

    MPRIS players (vlc, spotify, a browser) answer first — they play even when
    her own mpd is up; her library answers through mpc. A track on pause still
    has a name: «кто играет» may be asked about it too.
    """
    def split(out):
        artist, _, title = (out or '').strip().partition('\t')
        if artist or title:
            return artist, title
        return None

    if _player_status():
        r = _playerctl('metadata', '--format', '{{artist}}\t{{title}}')
        if r is not None and r.returncode == 0:
            names = split(r.stdout)
            if names:
                return names
    if _mpd_ready():
        r = _mpc('current', '-f', '%artist%\t%title%')
        if r is not None and r.returncode == 0:
            names = split(r.stdout)
            if names:
                return names
    return None


def service_start():
    """mpd comes up with her: the systemd user service, or a bare mpd when systemd is away"""
    if _mpd_ready():
        return True
    if shutil.which('systemctl'):
        try:
            r = subprocess.run(['systemctl', '--user', 'start', 'mpd.service'],
                               capture_output=True, timeout=8, env=ENV)
            if r.returncode == 0:
                return _ensure_mpd()
        except (OSError, subprocess.SubprocessError):
            pass
    return _ensure_mpd()               # a bare mpd reads ~/.config/mpd/mpd.conf itself


def service_stop():
    """mpd goes down with her: the playback dies first, then the daemon"""
    _playerctl('stop')
    _mpc('stop')
    if shutil.which('systemctl'):
        try:
            subprocess.run(['systemctl', '--user', 'stop', 'mpd.service'],
                           capture_output=True, timeout=8, env=ENV)
        except (OSError, subprocess.SubprocessError):
            pass
    pid = _running_processes.get('mpd')     # a bare mpd we spawned ourselves
    if pid:
        try:
            os.kill(pid, 15)
        except (OSError, ProcessLookupError):
            pass
        _running_processes['mpd'] = None


def _default_sink():
    """the system's default output sink name, or None when pactl is away"""
    if not shutil.which('pactl'):
        return None
    try:
        r = subprocess.run(['pactl', 'get-default-sink'], capture_output=True,
                           text=True, timeout=3, env=ENV)
    except (OSError, subprocess.SubprocessError):
        return None
    name = r.stdout.strip()
    return name if r.returncode == 0 and name else None


def _sink_volume(name=None):
    """the system output's volume percent (the quietest channel), or None"""
    try:
        r = subprocess.run(['pactl', 'get-sink-volume', name or '@DEFAULT_SINK@'],
                           capture_output=True, text=True, timeout=3, env=ENV)
    except (OSError, subprocess.SubprocessError):
        return None
    pcts = [int(m) for m in re.findall(r'/\s*(\d+)\s*%', r.stdout)]
    return min(pcts) if pcts else None


def read_volume():
    """the system loudness right now, in percent, or None when pactl is away"""
    name = _default_sink()
    return _sink_volume(name) if name else None


def system_volume(cmd):
    """The system loudness: 'volume_up' and 'volume_down' step it by 10,
    'volume N' sets it to N (10..100).

    Returns the new volume percent, or None when the knob would not turn.
    """
    name = _default_sink()
    if not name:
        return None
    cur = _sink_volume(name)
    if cur is None:
        cur = 50
    if cmd == 'volume_up':
        new = min(100, cur + 10)
    elif cmd == 'volume_down':
        new = max(0, cur - 10)
    else:
        try:                            # 'volume 50' from the voice parser
            new = int(str(cmd).split(None, 1)[1].strip('%'))
        except (ValueError, IndexError):
            return None
        new = min(100, max(10, new))    # «громкость 10-100»: the range she obeys
    try:
        r = subprocess.run(['pactl', 'set-sink-volume', name, f'{new}%'],
                           capture_output=True, timeout=3, env=ENV)
    except (OSError, subprocess.SubprocessError):
        return None
    return new if r.returncode == 0 else None


def music_control(cmd):
    """One entry for her voice and menu: 'play', 'pause', 'play_pause',
    'next', 'previous', 'stop', plus the system volume: 'volume_up',
    'volume_down', 'volume N' (10..100).

    «Включи музыку» ('play') is always her own library: never a toggle of some
    other player that happens to be around. The true toggle 'play_pause' is
    kept for the hotkey and the menu button. 'pause' only quiets what plays.
    """
    if cmd == 'stop':
        return stop_music(force=True)

    if cmd == 'play':                  # «включи музыку»: hers, from the top
        if is_mpd_playing():
            return True
        if _mpd_ready():               # her library was paused: resume it
            r = _mpc('play')
            return r is not None and r.returncode == 0 and is_mpd_playing()
        return play_music()            # nothing of hers going: start the library
                                       # (play_music silences the other players first)

    if cmd == 'pause':                 # «пауза»: quiet, without losing the track
        if _player_status().lower().startswith('playing'):
            r = _playerctl('pause')
            return r is not None and r.returncode == 0
        if '[playing]' in (get_mpd_status() or '').lower():
            r = _mpc('pause')
            return r is not None and r.returncode == 0
        return False

    if cmd == 'play_pause':           # the hotkey and the menu: a true toggle
        state = _player_status().lower()
        if state.startswith(('playing', 'paused')):   # a player with media: just toggle it
            r = _playerctl('play-pause')
            return r is not None and r.returncode == 0
        if _mpd_ready():                              # her own player is on: pause or resume
            _mpc('toggle')
            st = (get_mpd_status() or '').lower()
            if '[playing]' in st or '[paused]' in st:
                return True
        return play_music()                           # nothing going on: start her library

    if cmd == 'next':
        return next_track()

    if cmd == 'previous':
        return previous_track()

    if cmd in ('volume_up', 'volume_down') or cmd.startswith('volume '):
        return system_volume(cmd)      # «тише», «громче», «громкость 50»: the whole system

    return False

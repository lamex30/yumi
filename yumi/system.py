"""Watchers for the world around Yumi: power, Wi-Fi, Do Not Disturb, music, idle, wallpaper.

Everything here is optional: whatever tool is missing just means she does not react to that.
Long-running commands are followed in threads and restarted if they die (or given up on if
they keep dying right away); results are handed to the GTK main loop with GLib.idle_add.
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from gi.repository import Gio, GLib

from . import wallpaper

ENV = dict(os.environ)
ENV.pop('WAYLAND_SOCKET', None)
PS = Path('/sys/class/power_supply')


def run(*cmd, timeout=5):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=ENV).stdout
    except (OSError, subprocess.SubprocessError):
        return ''


def spawn(*cmd):
    """start a detached command; the Popen (truthy) or False"""
    try:
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                env=ENV, start_new_session=True)
    except OSError:
        return False


def main(fn, *args):
    GLib.idle_add(lambda: fn(*args) and False)


def stream(cmd, on_line, on_dead=None):
    """follow a command's output line by line; restart it when it ends. If it keeps dying
    within seconds (the compositor lacks what it needs), give up and call on_dead().
    False when the command is not installed."""
    if not shutil.which(cmd[0]):
        return False

    def loop():
        quick = 0
        while True:
            t0 = time.monotonic()
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        text=True, env=ENV)
                for line in proc.stdout:
                    on_line(line.strip())
                proc.wait()
            except OSError:
                pass
            quick = quick + 1 if time.monotonic() - t0 < 5 else 0
            if quick >= 3 and on_dead:
                on_dead()
                return
            time.sleep(3)
    threading.Thread(target=loop, daemon=True).start()
    return True


def read(p, default=''):
    try:
        return Path(p).read_text().strip()
    except OSError:
        return default


def power():
    """(plugged, capacity or None)"""
    plugged, cap = False, None
    if PS.is_dir():
        for d in PS.iterdir():
            t = read(d / 'type')
            if t == 'Mains' and read(d / 'online') == '1':
                plugged = True
            elif t == 'Battery' and cap is None:
                try:
                    cap = int(read(d / 'capacity', ''))
                except ValueError:
                    pass
    return plugged, cap


class World:
    """on: dict of callbacks charger(bool) battery_low(pct) wifi(ssid) dnd(bool) music(bool)
    palette(dict)"""

    def __init__(self, on):
        self.on = on
        self.state = {}
        self.plugged, self.cap = power()
        self.low_warned = False
        GLib.timeout_add_seconds(5, self.poll_power)
        stream(['nmcli', 'monitor'], lambda _l: self.wifi_soon())
        threading.Thread(target=self.wifi_check, daemon=True).start()
        self.notifier = Notifier(self.dnd_seen)
        stream(['playerctl', '--follow', 'status'], self.music_line)
        # a few minutes without input -> 'idle' (she may doze off at night), fifteen -> 'idle_long'
        self.can_idle = stream(['swayidle', '-w', 'timeout', '180', 'echo idle', 'timeout', '900',
                                'echo idle_long', 'resume', 'echo active'], self.idle_line,
                               on_dead=self.idle_dead)
        # the wallpaper: looked up now (so the settings know at once), then every few seconds
        self.wall = wallpaper.find()
        self.wall_sig = wallpaper.signature(self.wall)
        self.wall_palette = wallpaper.palette_for(self.wall)
        threading.Thread(target=self.watch_wallpaper, daemon=True).start()

    def emit(self, name, *args):
        fn = self.on.get(name)
        if fn:
            main(fn, *args)

    def changed(self, key, value):
        old = self.state.get(key, value)
        self.state[key] = value
        return old != value

    # --- power
    def poll_power(self):
        plugged, cap = power()
        if plugged != self.plugged:
            self.plugged = plugged
            self.emit('charger', plugged)
        self.cap = cap
        if cap is not None:
            low = not plugged and cap <= 15
            if low and not self.low_warned:
                self.emit('battery_low', cap)
            self.low_warned = low
        return True

    # --- wifi
    def wifi_soon(self):
        if not getattr(self, '_wifi_pending', False):
            self._wifi_pending = True
            threading.Timer(1.0, self.wifi_check).start()

    def wifi_check(self):
        self._wifi_pending = False
        out = run('nmcli', '-t', '-f', 'TYPE,STATE,CONNECTION', 'device')
        wifi = next((l.split(':', 2) for l in out.splitlines() if l.startswith('wifi:')), None)
        if wifi is None:
            return
        ssid = wifi[2] if len(wifi) > 2 and wifi[1] == 'connected' else ''
        if self.changed('wifi', ssid):
            self.emit('wifi', ssid)

    # --- do not disturb (whichever notification daemon is running)
    def dnd_seen(self, dnd):
        if 'dnd' not in self.state:
            self.state['dnd'] = dnd
            self.emit('dnd', dnd)
        elif self.changed('dnd', dnd):
            self.emit('dnd', dnd)

    def set_dnd(self, on):
        self.notifier.set(on)

    # --- you being away (swayidle, on any compositor with the idle protocol)
    def idle_line(self, line):
        if line in ('idle', 'idle_long', 'active'):
            self.emit('idle', line)

    def idle_dead(self):
        self.can_idle = False
        self.emit('caps')

    # --- music (MPRIS through playerctl)
    def music_line(self, line):
        playing = line.strip() == 'Playing'
        if self.changed('music', playing):
            self.emit('music', playing)
            self.cava_run(playing)

    def cava_run(self, on):
        """while music plays, cava's bass bars drive her dance: emit('beat', 0..1)"""
        proc, self.cava = getattr(self, 'cava', None), None
        if proc:
            proc.terminate()
        if not on or not shutil.which('cava'):
            return
        cfg = Path(os.environ.get('XDG_RUNTIME_DIR', '/tmp')) / 'yumi-cava.conf'
        cfg.write_text('[general]\nframerate=30\nbars=6\n[input]\nmethod=pulse\nsource=auto\n'
                       '[output]\nmethod=raw\ndata_format=ascii\nascii_max_range=100\n'
                       'bar_delimiter=59\nframe_delimiter=10\n')
        self.cava = proc = subprocess.Popen(['cava', '-p', str(cfg)], stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, text=True, env=ENV)

        def read():
            for line in proc.stdout:
                try:
                    bars = [int(v) for v in line.split(';') if v.strip()]
                except ValueError:
                    continue
                if bars:
                    self.emit('beat', max(bars[:2]) / 100)
            # shortcut: no restart if cava dies mid-song, she falls back to the sine sway
        threading.Thread(target=read, daemon=True).start()

    # --- wallpaper
    def watch_wallpaper(self):
        while True:
            time.sleep(4)
            try:
                found = wallpaper.find()
            except Exception:            # noqa: BLE001 - never let a weird setup kill the watcher
                continue
            sig = wallpaper.signature(found)
            if sig == self.wall_sig:
                continue
            had = self.wall is not None
            self.wall, self.wall_sig = found, sig
            pal = wallpaper.palette_for(found)
            self.wall_palette = pal
            if had != (found is not None):
                self.emit('caps')
            if pal:
                self.emit('palette', pal)


class Notifier:
    """Do Not Disturb of the notification daemon that is running: swaync, mako or dunst.
    Calls seen(bool) when it changes; set(bool) switches it."""

    def __init__(self, seen):
        self.seen = seen
        self.kind = None
        if stream(['swaync-client', '-swb'], self.swaync_line):
            self.kind = 'swaync'
        threading.Thread(target=self.poll, daemon=True).start()

    def swaync_line(self, line):
        try:
            dnd = 'dnd' in json.loads(line).get('class', '')
        except (ValueError, AttributeError):
            return
        self.kind = 'swaync'
        self.seen(dnd)

    def which(self):
        if self.kind == 'swaync':
            return 'swaync'
        if shutil.which('makoctl') and wallpaper._procs('mako'):
            return 'mako'
        if shutil.which('dunstctl') and wallpaper._procs('dunst'):
            return 'dunst'
        return None

    def poll(self):
        while True:
            kind = self.which()
            if kind == 'mako':
                self.seen('do-not-disturb' in run('makoctl', 'mode').split())
            elif kind == 'dunst':
                self.seen(run('dunstctl', 'is-paused').strip() == 'true')
            time.sleep(3 if kind in ('mako', 'dunst') else 15)

    def set(self, on):
        kind = self.which()
        if kind == 'swaync':
            spawn('swaync-client', '--dnd-on' if on else '--dnd-off', '--skip-wait')
        elif kind == 'mako':
            spawn('makoctl', 'mode', '-a' if on else '-r', 'do-not-disturb')
        elif kind == 'dunst':
            spawn('dunstctl', 'set-paused', 'true' if on else 'false')


def copy_text(text):
    if shutil.which('wl-copy'):
        try:
            p = subprocess.Popen(['wl-copy', text], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, env=ENV, start_new_session=True)
            return p is not None
        except OSError:
            return False
    return False


def can_pick():
    return bool(shutil.which('hyprpicker') or (shutil.which('slurp') and shutil.which('grim'))
                or os.environ.get('XDG_CURRENT_DESKTOP'))


def pick_color(done, accent=None):
    """let the user click a point on the screen; done('#rrggbb' or None) on the main loop.
    hyprpicker, then slurp + grim (wlroots), then the desktop portal (KDE, COSMIC...)"""
    def work():
        hexc = None
        desk = os.environ.get('XDG_CURRENT_DESKTOP', '').lower()
        if shutil.which('hyprpicker') and 'kde' not in desk:
            out = run('hyprpicker', '-f', 'hex', timeout=120).strip()
            hexc = out if re.fullmatch(r'#?[0-9a-fA-F]{6}', out or '') else None
            hexc = hexc and '#' + hexc.lstrip('#').lower()
        elif shutil.which('slurp') and shutil.which('grim') and 'kde' not in desk:
            col = (accent or '#bea7c8').lstrip('#')[:6]
            geo = run('slurp', '-p', '-b', '00000000', '-c', col + 'ff', timeout=120).strip()
            if geo:
                try:
                    data = subprocess.run(['grim', '-g', geo, '-t', 'ppm', '-'], capture_output=True,
                                          timeout=10, env=ENV).stdout
                    hexc = ppm_pixel(data)
                except (OSError, subprocess.SubprocessError):
                    hexc = None
        else:
            GLib.idle_add(lambda: portal_pick(done) and False)
            return
        main(done, hexc)
    threading.Thread(target=work, daemon=True).start()


def portal_pick(done):
    """org.freedesktop.portal.Screenshot.PickColor; done(hex or None)"""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error:
        done(None)
        return
    token = 'yumi%d' % int(time.monotonic() * 1000)
    sender = bus.get_unique_name()[1:].replace('.', '_')
    path = f'/org/freedesktop/portal/desktop/request/{sender}/{token}'
    state = {}

    def response(_c, _s, _p, _i, _sig, params, _d=None):
        bus.signal_unsubscribe(state['sub'])
        code, results = params.unpack()
        rgb = results.get('color') if code == 0 else None
        done('#%02x%02x%02x' % tuple(round(max(0, min(1, c)) * 255) for c in rgb) if rgb else None)

    state['sub'] = bus.signal_subscribe('org.freedesktop.portal.Desktop', 'org.freedesktop.portal.Request',
                                        'Response', path, None, Gio.DBusSignalFlags.NO_MATCH_RULE, response)

    def called(conn, res):
        try:
            conn.call_finish(res)
        except GLib.Error:
            bus.signal_unsubscribe(state['sub'])
            done(None)
    bus.call('org.freedesktop.portal.Desktop', '/org/freedesktop/portal/desktop',
             'org.freedesktop.portal.Screenshot', 'PickColor',
             GLib.Variant('(sa{sv})', ('', {'handle_token': GLib.Variant('s', token)})),
             None, Gio.DBusCallFlags.NONE, -1, None, called)


def ppm_pixel(data):
    """first pixel of a binary PPM (P6) as #rrggbb"""
    if not data or not data.startswith(b'P6'):
        return None
    fields, i = [], 2
    while len(fields) < 3 and i < len(data):
        while i < len(data) and data[i:i + 1].isspace():
            i += 1
        if data[i:i + 1] == b'#':
            while i < len(data) and data[i:i + 1] != b'\n':
                i += 1
            continue
        j = i
        while j < len(data) and not data[j:j + 1].isspace():
            j += 1
        fields.append(int(data[i:j]))
        i = j
    i += 1
    mx = fields[2] if len(fields) == 3 else 255
    if mx > 255:
        px = [int.from_bytes(data[i + 2 * k:i + 2 * k + 2], 'big') * 255 // mx for k in range(3)]
    else:
        px = [data[i + k] * 255 // mx for k in range(3)]
    return '#%02x%02x%02x' % tuple(px)


def play_file(path):
    """play a sound file in the background with whatever player is around"""
    path = str(path)
    for cmd in (['pw-play', path], ['paplay', path], ['aplay', '-q', path],
                ['ffplay', '-nodisp', '-autoexit', '-loglevel', 'quiet', path]):
        if shutil.which(cmd[0]):
            return spawn(*cmd)
    return False

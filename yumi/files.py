"""Recent files: the user folders watched with Gio file monitors plus recently-used.xbel.

Callbacks: changed() whenever the list changes, download(path) when a download finishes in
~/Downloads, screenshot(path) when a new picture lands in ~/Pictures/Screenshots.
"""
import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlparse

from gi.repository import Gio, GLib

KEEP = 24                      # entries kept in memory
PARTIAL = ('.part', '.crdownload', '.tmp', '.download', '.partial', '~', '.swp', '.kate-swp')
XBEL = Path(GLib.get_user_data_dir()) / 'recently-used.xbel'


def user_dirs():
    names = [GLib.UserDirectory.DIRECTORY_DOWNLOAD, GLib.UserDirectory.DIRECTORY_PICTURES,
             GLib.UserDirectory.DIRECTORY_DOCUMENTS, GLib.UserDirectory.DIRECTORY_DESKTOP,
             GLib.UserDirectory.DIRECTORY_VIDEOS, GLib.UserDirectory.DIRECTORY_MUSIC]
    out = []
    home = Path.home()
    for n in names:
        p = GLib.get_user_special_dir(n)
        if p and Path(p) != home and Path(p).is_dir():
            out.append(Path(p))
    if not out:
        out = [d for d in (home / 'Downloads', home / 'Pictures', home / 'Documents') if d.is_dir()]
    shots = Path(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES) or
                 home / 'Pictures') / 'Screenshots'
    if shots.is_dir() and shots not in out:
        out.append(shots)
    return out


def usable(p):
    name = p.name
    return (not name.startswith('.') and not name.endswith(PARTIAL) and p.is_file())


def read_xbel(limit=40):
    """(path, mtime) of recently used local files"""
    out = []
    try:
        root = ET.parse(XBEL).getroot()
    except (OSError, ET.ParseError):
        return out
    for b in root.iter('bookmark'):
        href = b.get('href', '')
        if not href.startswith('file://'):
            continue
        p = Path(unquote(urlparse(href).path))
        when = b.get('visited') or b.get('modified') or ''
        try:
            t = time.mktime(time.strptime(when[:19], '%Y-%m-%dT%H:%M:%S')) - time.timezone
        except ValueError:
            t = 0
        out.append((p, t))
    out.sort(key=lambda x: -x[1])
    return out[:limit]


class Recent:
    def __init__(self, changed=None, download=None, screenshot=None):
        self.changed = changed or (lambda: None)
        self.download = download or (lambda p: None)
        self.screenshot = screenshot or (lambda p: None)
        self.items = {}          # path -> time
        self.monitors = []
        self.dirs = user_dirs()
        self.downloads = Path(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
                              or Path.home() / 'Downloads')
        self.shots = next((d for d in self.dirs if d.name == 'Screenshots'), None)
        self.started = time.monotonic()
        self.announced = set()
        self.scan()
        for d in self.dirs:
            try:
                m = Gio.File.new_for_path(str(d)).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
                m.connect('changed', self.on_event)
                self.monitors.append(m)
            except GLib.Error:
                pass
        try:
            m = Gio.File.new_for_path(str(XBEL)).monitor_file(Gio.FileMonitorFlags.NONE, None)
            m.connect('changed', lambda *_: self.merge_xbel(notify=True))
            self.monitors.append(m)
        except GLib.Error:
            pass

    def scan(self):
        for d in self.dirs:
            try:
                for p in d.iterdir():
                    if usable(p):
                        self.items[p] = p.stat().st_mtime
            except OSError:
                pass
        self.merge_xbel(notify=False)
        self.trim()

    def merge_xbel(self, notify=True):
        for p, t in read_xbel():
            if p.exists() and usable(p):
                self.items[p] = max(self.items.get(p, 0), t)
        self.trim()
        if notify:
            self.changed()

    def trim(self):
        for p in [p for p in self.items if not p.exists()]:
            del self.items[p]
        if len(self.items) > KEEP:
            for p, _ in sorted(self.items.items(), key=lambda x: x[1])[:len(self.items) - KEEP]:
                del self.items[p]

    def add(self, path):
        p = Path(path)
        if p.exists():
            self.items[p] = time.time()
            self.trim()
            self.changed()

    def list(self, n=8):
        self.trim()
        return [p for p, _ in sorted(self.items.items(), key=lambda x: -x[1])][:n]

    def on_event(self, _mon, f, other, ev):
        E = Gio.FileMonitorEvent
        if ev == E.RENAMED and other is not None:          # foo.part -> foo: a finished download
            old, p = Path(f.get_path()), Path(other.get_path())
            self.items.pop(old, None)
        elif ev in (E.CHANGES_DONE_HINT, E.CREATED, E.MOVED_IN):
            p = Path(f.get_path())
        elif ev in (E.DELETED, E.MOVED_OUT):
            if self.items.pop(Path(f.get_path()), None) is not None:
                self.changed()
            return
        else:
            return
        if not usable(p):
            return
        self.items[p] = time.time()
        self.trim()
        self.changed()
        # announce once, when the file is complete (CREATED comes while it is still being written)
        if time.monotonic() - self.started < 3 or ev == E.CREATED or p in self.announced:
            return
        self.announced.add(p)
        if self.shots and p.parent == self.shots:
            self.screenshot(p)
        elif p.parent == self.downloads:
            self.download(p)


def open_path(path):
    try:
        Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(str(path)).get_uri(), None)
        return True
    except GLib.Error:
        try:
            Gio.Subprocess.new(['xdg-open', str(path)], Gio.SubprocessFlags.NONE)
            return True
        except GLib.Error:
            return False


def short(name, n=22):
    name = os.path.basename(str(name))
    return name if len(name) <= n else name[:n - 1] + '…'

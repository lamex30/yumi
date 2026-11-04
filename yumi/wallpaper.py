"""Where the wallpaper is, and what color it is (no GTK here, GdkPixbuf only to read images).

Yumi takes the hue of the wallpaper. She looks, in this order, for:

  1. a palette file written by a theming script: ~/.config/yumi/palette.json (a pastel-rice
     style palette, or just {"hue": 0..1}), or ~/.config/pastel-rice/palette.json;
  2. the image a running wallpaper daemon shows: awww / swww, swaybg, hyprpaper, wpaperd;
  3. the image in a known config: waypaper, azote, KDE Plasma, COSMIC, pywal.

If none of that is there, following the wallpaper is "not available yet" and the colors are
picked by hand in the settings.
"""
import colorsys
import json
import math
import os
import re
import shutil
import subprocess
from pathlib import Path

HOME = Path.home()
CFG = Path(os.environ.get('XDG_CONFIG_HOME') or HOME / '.config')
CACHE = Path(os.environ.get('XDG_CACHE_HOME') or HOME / '.cache')

PALETTE_FILES = (CFG / 'yumi' / 'palette.json', CFG / 'pastel-rice' / 'palette.json')
IMAGE_RE = re.compile(r'(?:file://)?((?:~|/)[^\s"\'<>|,;=()]+?\.(?:png|jpe?g|webp|gif|bmp|avif|jxl|tiff?))',
                      re.IGNORECASE)


def _run(*cmd, timeout=2):
    if not shutil.which(cmd[0]):
        return ''
    env = dict(os.environ)
    env.pop('WAYLAND_SOCKET', None)
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env).stdout
    except (OSError, subprocess.SubprocessError):
        return ''


def _read(p):
    try:
        return Path(p).read_text(errors='replace')
    except OSError:
        return ''


def images_in(text):
    """existing image files mentioned in a piece of text, in order"""
    out = []
    for m in IMAGE_RE.finditer(text or ''):
        p = Path(os.path.expanduser(m.group(1)))
        if p.is_file() and p not in out:
            out.append(p)
    return out


def _first(text):
    found = images_in(text)
    return found[0] if found else None


# ---- the running daemons
def from_awww():
    for tool in ('awww', 'swww'):
        out = _run(tool, 'query')
        hit = _first(out.replace('image: ', ' '))
        if hit:
            return hit
    return None


def _procs(name):
    """command lines (lists) of the running processes called `name`"""
    out = []
    for d in Path('/proc').iterdir() if Path('/proc').is_dir() else ():
        if not d.name.isdigit():
            continue
        try:
            if (d / 'comm').read_text().strip() != name:
                continue
            out.append([a.decode(errors='replace') for a in (d / 'cmdline').read_bytes().split(b'\0') if a])
        except OSError:
            continue
    return out


def from_swaybg():
    for args in _procs('swaybg'):
        for i, a in enumerate(args):
            if a in ('-i', '--image') and i + 1 < len(args):
                p = Path(os.path.expanduser(args[i + 1]))
                if p.is_file():
                    return p
            elif a.startswith('--image='):
                p = Path(os.path.expanduser(a.split('=', 1)[1]))
                if p.is_file():
                    return p
    return None


def from_hyprpaper():
    if not _procs('hyprpaper'):
        return None
    return _first(_run('hyprctl', 'hyprpaper', 'listactive')) or \
        _first(_read(CFG / 'hypr' / 'hyprpaper.conf'))


def from_wpaperd():
    if not _procs('wpaperd'):
        return None
    return _first(_run('wpaperctl', 'all-wallpapers')) or _first(_run('wpaperctl', 'get-wallpaper'))


DAEMONS = (('awww', from_awww), ('swaybg', from_swaybg), ('hyprpaper', from_hyprpaper),
           ('wpaperd', from_wpaperd))


# ---- known configs
def from_kde():
    text = _read(CFG / 'plasma-org.kde.plasma.desktop-appletsrc')
    for m in re.finditer(r'^Image=(.+)$', text, re.MULTILINE):
        val = m.group(1).strip()
        p = Path(val[7:] if val.startswith('file://') else os.path.expanduser(val))
        if p.is_file():
            return p
        if p.is_dir():                                   # a wallpaper package: the largest image
            imgs = sorted((q for q in (p / 'contents' / 'images').glob('*') if IMAGE_RE.search('/' + q.name)),
                          key=lambda q: q.stat().st_size, reverse=True)
            if imgs:
                return imgs[0]
    return None


CONFIGS = (
    ('waypaper', lambda: _first(_read(CFG / 'waypaper' / 'config.ini'))),
    ('azote', lambda: _first(_read(HOME / '.azotebg'))),
    ('kde', from_kde),
    ('cosmic', lambda: _first(_read(CFG / 'cosmic' / 'com.system76.CosmicBackground' / 'v1' / 'all'))),
    ('pywal', lambda: _first(_read(CACHE / 'wal' / 'wal'))),
)


def find():
    """('palette', Path) for a palette file, ('image', Path, source) for a wallpaper, or None"""
    for p in PALETTE_FILES:
        if p.is_file():
            return ('palette', p, p.parent.name)
    for name, fn in DAEMONS + CONFIGS:
        try:
            hit = fn()
        except OSError:
            hit = None
        if hit:
            return ('image', hit, name)
    return None


# ---- the color of a picture
def dominant_hue(pixels):
    """the hue (0..1) most of the colorful pixels share, or None for a grey picture.
    pixels: iterable of (r, g, b) 0..255"""
    bins = [0.0] * 36
    sums = [[0.0, 0.0] for _ in range(36)]
    total = n = 0
    for r, g, b in pixels:
        n += 1
        h, l, s = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
        chroma = s * (1 - abs(2 * l - 1))
        if chroma < 0.06:
            continue
        k = int(h * 36) % 36
        bins[k] += chroma
        sums[k][0] += chroma * math.cos(h * 6.283185307)
        sums[k][1] += chroma * math.sin(h * 6.283185307)
        total += chroma
    if not n or total < 0.015 * n:
        return None
    smooth = [0.25 * bins[k - 1] + 0.5 * bins[k] + 0.25 * bins[(k + 1) % 36] for k in range(36)]
    best = max(range(36), key=lambda k: smooth[k])
    x = sum(sums[(best + d) % 36][0] for d in (-1, 0, 1))
    y = sum(sums[(best + d) % 36][1] for d in (-1, 0, 1))
    return (math.atan2(y, x) / (2 * math.pi)) % 1.0


def image_hue(path):
    """dominant hue of an image file (read small with GdkPixbuf), or None"""
    try:
        import gi
        gi.require_version('GdkPixbuf', '2.0')
        from gi.repository import GdkPixbuf
        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), 96, 96, True)
    except Exception:            # noqa: BLE001 - any broken or odd image just has no hue
        return None
    w, h, rs, nc = pb.get_width(), pb.get_height(), pb.get_rowstride(), pb.get_n_channels()
    data = pb.get_pixels()
    return dominant_hue((data[y * rs + x * nc], data[y * rs + x * nc + 1], data[y * rs + x * nc + 2])
                        for y in range(h) for x in range(w))


def load_palette_file(path):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if set(data) == {'hue'} or 'bg' not in data:          # a bare {"hue": ...}
        from .sprites import palette_from_hue
        hue = data.get('hue')
        return palette_from_hue(float(hue) % 1.0 if isinstance(hue, (int, float)) else None)
    return data


def signature(found):
    """what to compare to notice a change: the file and its mtime"""
    if not found:
        return None
    try:
        return (found[0], str(found[1]), found[1].stat().st_mtime)
    except OSError:
        return (found[0], str(found[1]), 0)


def palette_for(found):
    """the palette (dict) for what find() returned"""
    from .sprites import palette_from_hue
    if not found:
        return None
    if found[0] == 'palette':
        return load_palette_file(found[1])
    return palette_from_hue(image_hue(found[1]))

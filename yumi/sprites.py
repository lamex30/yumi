"""Sprite metadata and the wallpaper recolor (pure Python: no GTK, no numpy).

Every pose frame is described in "body-local" coordinates: the standing body sprite's top-left
pixel is (0, 0). poses.json gives, per frame, where that origin lands inside the frame image
(dx, dy), so wings and antennae from the standing rig attach to any pose with the same offsets.
"""
import colorsys
import json
import struct
from pathlib import Path

BASE_HUE = 0.80          # Yumi's own mauve/lilac, what the wallpaper hue replaces


def png_size(path):
    with open(path, 'rb') as f:
        head = f.read(24)
    w, h = struct.unpack('>II', head[16:24])
    return w, h


class Meta:
    """sizes and offsets of everything; `names` maps a sprite key to its PNG file"""

    def __init__(self, folder, outfit=None):
        self.dir = Path(folder)
        self.rig = json.loads((self.dir / 'rig.json').read_text())
        self.poses = json.loads((self.dir / 'poses.json').read_text())['poses']
        self.moth = json.loads((self.dir / 'moth' / 'moth.json').read_text())
        self.faces = json.loads((self.dir / 'faces' / 'faces.json').read_text())['faces']
        b = self.rig['body']
        self.bx, self.by, self.bw, self.bh = b['x'], b['y'], b['w'], b['h']
        self.names = {'body': 'body.png', 'eyes': 'eyes.png', 'moth': 'moth/moth.png'}
        for n in self.rig['parts']:
            self.names[n] = f'{n}.png'
        for f in self.faces:
            self.names[f'face:{f}'] = f'faces/{f}.png'
        for d in ('l', 'r'):
            if (self.dir / 'gaze' / f'{d}.png').is_file():
                self.names[f'gaze:{d}'] = f'gaze/{d}.png'
        for pose, p in self.poses.items():
            for i, fr in enumerate(p['frames']):
                self.names[f'{pose}:{i}'] = fr['img']
            for h, img in p.get('heads', {}).items():
                self.names[f'{pose}:head:{h}'] = img
        extra = self.dir / 'props'
        if extra.is_dir():
            for f in extra.glob('*.png'):
                self.names[f'prop:{f.stem}'] = f'props/{f.name}'
        # an outfit is a folder in outfits/ that replaces any of the files above (same names,
        # same size) and may add overlays (pumpkin by her feet) and fliers (bats around her)
        self.paths = {k: self.dir / v for k, v in self.names.items()}
        self.outfit, self.fx, self.fw, self.keep = None, {}, {}, set()
        od = self.dir / 'outfits' / outfit if outfit else None
        if od and od.is_dir():
            self.outfit = outfit
            for k, v in self.names.items():
                if (od / v).is_file():
                    self.paths[k] = od / v
            try:
                self.fx = json.loads((od / 'outfit.json').read_text())
            except (OSError, ValueError):
                self.fx = {}
            for c in self.fx.get('keep', []):
                try:
                    self.keep.add(tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)))
                except (TypeError, ValueError):
                    pass
            for ov in self.fx.get('overlays', []):
                if (od / ov['img']).is_file():
                    key = f"acc:{Path(ov['img']).stem}"
                    self.names[key] = f"outfits/{outfit}/{ov['img']}"
                    self.paths[key] = od / ov['img']
            hat = self.fx.get('hat')
            if hat:
                for part, key in (('img', 'hat'), ('front', 'hat:front')):
                    if hat.get(part) and (od / hat[part]).is_file():
                        self.names[key] = f"outfits/{outfit}/{hat[part]}"
                        self.paths[key] = od / hat[part]
            fl = self.fx.get('fliers')
            if fl and (od / fl['img']).is_file():
                key = f"acc:{Path(fl['img']).stem}"
                self.names[key] = f"outfits/{outfit}/{fl['img']}"
                self.paths[key] = od / fl['img']
                self.fw[key] = fl['fw']
        self.size = {k: png_size(v) for k, v in self.paths.items()}
        # crown of the standing body: where her scruff is when she is picked up
        self.crown = self._crown()

    @staticmethod
    def outfits(folder):
        """names of the outfit folders that are there"""
        d = Path(folder) / 'outfits'
        return sorted(p.name for p in d.iterdir() if p.is_dir()) if d.is_dir() else []

    def _crown(self):
        return (self.bw / 2.0, 0.0)

    def part(self, name):
        """(x, y) body-local of a rig part, its frame size and pivot"""
        p = self.rig['parts'][name]
        return p['x'] - self.bx, p['y'] - self.by, p['w'], p['h'], p['pivot']

    def frame(self, pose, i):
        """(key, dx, dy, w, h) of frame i of a pose"""
        fr = self.poses[pose]['frames'][i]
        key = f'{pose}:{i}'
        w, h = self.size[key]
        return key, fr['dx'], fr['dy'], w, h


# ---- wallpaper recolor ---------------------------------------------------------------------

def recolorable(h, l, s):
    """the mauve/lilac/purple parts: hair, sleeves, skirt, stripes, boots, wings, outline.
    Skin, cream, whites, the pink bow and laces, Tux's black and yellow stay as they are."""
    if s < 0.10 or l > 0.97 or l < 0.06:
        return False
    if 0.60 <= h <= 0.925:
        return True
    if 0.925 < h <= 0.99 and l < 0.62:          # dark mauve hair shadows lean pink
        return True
    return False


def shift(rgb, delta, sat=1.0):
    r, g, b = (c / 255.0 for c in rgb)
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    if not recolorable(h, l, s):
        return rgb
    h = (h + delta) % 1.0
    r, g, b = colorsys.hls_to_rgb(h, l, min(1.0, s * sat))
    return int(round(r * 255)), int(round(g * 255)), int(round(b * 255))


def palette_delta(pal):
    """hue rotation for a pastel-rice palette.json (its 'hue' is 0..1), or None"""
    if not pal:
        return None
    hue = pal.get('hue')
    if not isinstance(hue, (int, float)):
        return None
    d = (float(hue) - BASE_HUE) % 1.0
    return None if min(d, 1 - d) < 1e-4 else d


SWATCHES = [None, 0.95, 0.0, 0.07, 0.14, 0.33, 0.47, 0.55, 0.62, 0.72]   # None = her own colors


def swatch_hex(hue):
    h = BASE_HUE if hue is None else hue
    r, g, b = colorsys.hls_to_rgb(h, 0.72, 0.45)
    return '#%02x%02x%02x' % (round(r * 255), round(g * 255), round(b * 255))


def palette_from_hue(hue):
    """a pastel-rice style palette built from one hue (for the manual color choice)"""
    h = BASE_HUE if hue is None else hue

    def c(l, s, dh=0.0):
        r, g, b = colorsys.hls_to_rgb((h + dh) % 1.0, l, s)
        return '#%02x%02x%02x' % (round(r * 255), round(g * 255), round(b * 255))
    return {'bg': c(0.07, 0.10), 'surface': c(0.11, 0.10), 'raised': c(0.15, 0.10),
            'selection': c(0.21, 0.10), 'border': c(0.26, 0.10), 'muted': c(0.56, 0.08),
            'fg': c(0.83, 0.07), 'bright': c(0.92, 0.05), 'accent': c(0.68, 0.22),
            'accent_dim': c(0.46, 0.15), 'magenta': c(0.70, 0.25, 0.04), 'red': c(0.73, 0.27, 0.95 - h),
            'green': c(0.72, 0.22, 0.33 - h), 'yellow': c(0.75, 0.25, 0.14 - h),
            'blue': c(0.72, 0.22, 0.60 - h), 'cyan': c(0.72, 0.22, 0.48 - h),
            'hue': None if hue is None else hue}


def recolor_bgra(buf, delta, sat=0.9, keep=()):
    """in place: a cairo ARGB32 buffer (little endian B,G,R,A; alpha is 0 or 255 in our art).
    `keep`: (r, g, b) colors that stay as they are (an outfit's red sweater, not her hair)"""
    mv = memoryview(buf).cast('B')
    cache = {k: k for k in keep}
    n = len(mv) // 4
    for i in range(n):
        o = i * 4
        if mv[o + 3] == 0:
            continue
        key = (mv[o + 2], mv[o + 1], mv[o])
        new = cache.get(key)
        if new is None:
            new = cache[key] = shift(key, delta, sat)
        if new != key:
            mv[o + 2], mv[o + 1], mv[o] = new
    return buf


def ui_colors(pal):
    """bubble / card colors (r, g, b) 0..1 from the palette, with a lavender fallback"""
    def rgb(hexs, fallback):
        try:
            hexs = hexs.lstrip('#')
            return tuple(int(hexs[i:i + 2], 16) / 255 for i in (0, 2, 4))
        except (AttributeError, ValueError):
            return fallback
    pal = pal or {}
    return {
        'panel': rgb(pal.get('surface'), (0.16, 0.13, 0.20)),
        'raised': rgb(pal.get('raised'), (0.22, 0.18, 0.27)),
        'sel': rgb(pal.get('selection'), (0.30, 0.25, 0.37)),
        'border': rgb(pal.get('border'), (0.42, 0.35, 0.50)),
        'fg': rgb(pal.get('fg'), (0.90, 0.86, 0.94)),
        'muted': rgb(pal.get('muted'), (0.62, 0.56, 0.68)),
        'accent': rgb(pal.get('magenta') or pal.get('accent'), (0.78, 0.66, 0.86)),
        'accent2': rgb(pal.get('accent'), (0.66, 0.74, 0.86)),
        'red': rgb(pal.get('red'), (0.85, 0.6, 0.6)),
        'green': rgb(pal.get('green'), (0.65, 0.82, 0.62)),
        'yellow': rgb(pal.get('yellow'), (0.86, 0.82, 0.6)),
        'blue': rgb(pal.get('blue'), (0.62, 0.72, 0.86)),
        'cyan': rgb(pal.get('cyan'), (0.62, 0.82, 0.8)),
    }


KIND_COLOR = {'image': 'blue', 'video': 'red', 'audio': 'green', 'doc': 'yellow', 'other': 'muted',
              'archive': 'cyan'}
DOCS = {'pdf', 'txt', 'md', 'doc', 'docx', 'odt', 'rtf', 'xls', 'xlsx', 'ods', 'ppt', 'pptx',
        'odp', 'csv', 'json', 'html', 'epub'}
ARCHIVES = {'zip', 'tar', 'gz', 'xz', 'zst', '7z', 'rar', 'bz2', 'tgz'}

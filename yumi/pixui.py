"""Pixel-art interface pieces as plain rectangles (pure Python).

Every UI op from the brain (panels, buttons, switches, bubble tails, icons, progress bars)
is expanded here into filled rectangles on a grid of `P` screen pixels, so the GTK window and
the PIL test renderer draw exactly the same chunky pixels.

Look: dark outline with notched (rounded) corners, a light inner edge on top/left, a shade on
the bottom/right, and a solid "thickness" under buttons, like 16-bit game menus.
"""


def mix(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def shades(col):
    """the palette-derived colors the pieces use; `col` is sprites.ui_colors()"""
    bg = mix(col['panel'], (0, 0, 0), 0.35)
    fill = col['raised']
    acc = col['accent']
    return {
        'line': mix(bg, (0, 0, 0), 0.45),
        'fill': fill,
        'hi': mix(fill, col['fg'], 0.22),
        'shade': mix(fill, (0, 0, 0), 0.35),
        'hot': mix(fill, col['fg'], 0.12),
        'deep': mix(fill, (0, 0, 0), 0.22),
        'acc': acc,
        'acc_hi': mix(acc, (1, 1, 1), 0.35),
        'acc_sh': mix(acc, (0, 0, 0), 0.35),
        'acc_dim': mix(fill, acc, 0.45),
        'paper': mix(col['fg'], fill, 0.12),
        'shadow': (0, 0, 0),
        'white': (1, 1, 1),
        'heart': (1.0, 0.45, 0.62),
        'fg': col['fg'],
        'muted': col['muted'],
    }


STYLES = {
    #          fill       hi        shade     thickness (P rows under it)
    'window': ('fill', 'hi', 'shade', 0),
    'bubble': ('paper', None, 'hot', 0),
    'card': ('fill', 'hi', 'shade', 1),
    'card_hot': ('hot', 'hi', 'shade', 1),
    'button': ('fill', 'hi', 'shade', 1),
    'button_hot': ('acc_dim', 'acc_hi', 'acc_sh', 1),
    'tab': ('deep', 'hi', 'shade', 0),
    'tab_on': ('acc_dim', 'acc_hi', 'acc_sh', 0),
    'row_hot': ('acc_dim', None, None, 0),
    'inset': ('deep', None, 'hi', 0),
}


def panel(x, y, w, h, style, P):
    """rects (x, y, w, h, color_key, alpha) of a pixel panel; (x, y, w, h) is its outer box,
    the thickness of buttons is drawn inside the box at the bottom"""
    fill, hi, sh, thick = STYLES.get(style, STYLES['window'])
    x, y = int(round(x / P)) * P, int(round(y / P)) * P
    w, h = max(4, int(round(w / P))) * P, max(4, int(round(h / P))) * P
    r = []
    if style in ('window', 'bubble', 'card', 'card_hot'):
        r.append((x + 2 * P, y + h, w - 3 * P, P, 'shadow', 0.28))        # soft drop shadow
    if style in ('row_hot', 'inset'):
        r.append((x + P, y, w - 2 * P, h, fill, 1.0))
        r.append((x, y + P, w, h - 2 * P, fill, 1.0))
        if sh:
            r.append((x + P, y, w - 2 * P, P, 'shade', 1.0))
        return r
    body_h = h - thick * P
    # interior
    r.append((x + P, y + P, w - 2 * P, h - 2 * P, fill, 0.97))
    if thick:
        r.append((x + P, y + body_h - P, w - 2 * P, thick * P, sh, 1.0))
    if hi:
        r.append((x + 2 * P, y + P, w - 4 * P, P, hi, 1.0))
        r.append((x + P, y + 2 * P, P, body_h - 4 * P, hi, 1.0))
    if sh and not thick:
        r.append((x + 2 * P, y + h - 2 * P, w - 4 * P, P, sh, 1.0))
        r.append((x + w - 2 * P, y + 2 * P, P, h - 4 * P, sh, 1.0))
    # outline with notched corners
    r += [(x + 2 * P, y, w - 4 * P, P, 'line', 1.0), (x + 2 * P, y + h - P, w - 4 * P, P, 'line', 1.0),
          (x, y + 2 * P, P, h - 4 * P, 'line', 1.0), (x + w - P, y + 2 * P, P, h - 4 * P, 'line', 1.0),
          (x + P, y + P, P, P, 'line', 1.0), (x + w - 2 * P, y + P, P, P, 'line', 1.0),
          (x + P, y + h - 2 * P, P, P, 'line', 1.0), (x + w - 2 * P, y + h - 2 * P, P, P, 'line', 1.0)]
    if thick:
        r.append((x + P, y + body_h - P, w - 2 * P, P, 'line', 0.55))   # edge between top and side
    return r


def tail(x, y, direction, P, fill='paper'):
    """speech-bubble tail hanging from (x, y) (the bubble edge), 3 steps"""
    # (x, y) is the middle of the bubble's outer bottom edge (or top edge when pointing up)
    x = int(round(x / P)) * P
    y = int(round(y / P)) * P
    r = []
    # rows: (row offset, outline width, fill width) in P; row 0 is the bubble's own outline row
    rows = [(0, 0, 3), (1, 5, 3), (2, 3, 1), (3, 1, 0)]
    for k, lw, fw in rows:
        yy = y + (k - 1) * P if direction == 'down' else y - k * P
        if lw:
            r.append((x - (lw // 2) * P, yy, lw * P, P, 'line', 1.0))
        if fw:
            r.append((x - (fw // 2) * P, yy, fw * P, P, fill, 1.0))
    return r


def switch(x, y, on, P):
    """a little toggle: 14 x 7 P, rounded track with a square knob inside"""
    x, y = int(round(x / P)) * P, int(round(y / P)) * P
    w, h = 14 * P, 7 * P
    track = 'acc' if on else 'deep'
    r = [(x + P, y, w - 2 * P, h, 'line', 1.0), (x, y + P, w, h - 2 * P, 'line', 1.0),
         (x + P, y + P, w - 2 * P, h - 2 * P, track, 1.0)]
    kx = x + w - 6 * P if on else x + P
    r += [(kx, y + P, 5 * P, 5 * P, 'line', 1.0), (kx + P, y + 2 * P, 3 * P, 3 * P, 'paper', 1.0),
          (kx + P, y + 2 * P, 3 * P, P, 'fg', 1.0)]
    return r


def bar(x, y, w, frac, P):
    """segmented progress bar, 3 P tall"""
    x, y = int(round(x / P)) * P, int(round(y / P)) * P
    n = max(1, int(w // (3 * P)))
    lit = int(round(frac * n))
    r = []
    for i in range(n):
        sx = x + i * 3 * P
        r.append((sx, y, 2 * P, 3 * P, 'acc' if i < lit else 'deep', 1.0))
    return r


ICONS = {
    'convert': ['.....#.', '######.', '.....#.', '.#.....', '.######', '.#.....', '.......'],
    'compress': ['...#...', '.#.#.#.', '..###..', '...#...', '.......', '#######', '.......'],
    'star': ['...#...', '..#.#..', '##...##', '.#...#.', '..#.#..', '.#.#.#.', '##...##'],
    'star_on': ['...#...', '..###..', '#######', '.#####.', '..###..', '.##.##.', '##...##'],
    'image': ['#######', '#.....#', '#.#...#', '#....##', '#.#.###', '#######', '.......'],
    'video': ['#######', '#.#.#.#', '#######', '#.....#', '#######', '#.#.#.#', '#######'],
    'audio': ['...###.', '...#.#.', '...#...', '...#...', '.###...', '####...', '.##....'],
    'doc': ['####...', '#..##..', '#...##.', '#.....#', '#.###.#', '#.....#', '#######'],
    'archive': ['#######', '#..#..#', '#######', '#..#..#', '#..#..#', '#..#..#', '#######'],
    'other': ['#####..', '#...##.', '#....#.', '#....#.', '#....#.', '#....#.', '######.'],
    'back': ['..#....', '.##....', '#######', '.##....', '..#....', '.......', '.......'],
    'next': ['....#..', '....##.', '#######', '....##.', '....#..', '.......', '.......'],
    'moon': ['..###..', '.##....', '##.....', '##.....', '##.....', '.##....', '..###..'],
    'heart': ['.##.##.', '#######', '#######', '.#####.', '..###..', '...#...', '.......'],
    'dnd': ['..###..', '.#...#.', '#.....#', '#.###.#', '#.....#', '.#...#.', '..###..'],
    'drop': ['...#...', '..###..', '.#####.', '.#####.', '.##.##.', '..###..', '.......'],
    'gear': ['.#.#.#.', '#######', '.##.##.', '###.###', '.##.##.', '#######', '.#.#.#.'],
    'home': ['...#...', '..###..', '.#####.', '#######', '.#...#.', '.#.#.#.', '.#####.'],
    'box': ['#######', '#..#..#', '#..#..#', '#######', '#.....#', '#.....#', '#######'],
    'shelf': ['#######', '.#...#.', '.#.#.#.', '#######', '.#...#.', '.#...#.', '#######'],
    'restart': ['..###..', '.#...#.', '#...###', '#....#.', '#.....#', '.#...#.', '..###..'],
    'quit': ['...#...', '.#.#.#.', '#..#..#', '#.....#', '#.....#', '.#...#.', '..###..'],
    'note': ['...###.', '...#.#.', '...#.#.', '...#.#.', '.###.#.', '####...', '.##....'],
    'play': ['.#.....', '.##....', '.###...', '.####..', '.###...', '.##....', '.#.....'],
    'clock': ['..###..', '.#.#.#.', '#..#..#', '#..##.#', '#.....#', '.#...#.', '..###..'],
}


def circle(x, y, d, P, hot=False, danger=False):
    """a round pixel button of diameter d (screen px): outline, fill, light top, shaded bottom"""
    import math
    x, y = int(round(x / P)) * P, int(round(y / P)) * P
    n = max(5, int(round(d / P)))
    r = n / 2.0
    fill = 'acc_dim' if hot else 'fill'
    hi = 'acc_hi' if hot else 'hi'
    sh = 'acc_sh' if hot else 'shade'
    out = []

    def spans(rr, off):
        res = {}
        for j in range(n):
            yy = j + 0.5 - r - off
            if rr * rr - yy * yy > 0:
                w = math.sqrt(rr * rr - yy * yy)
                a = int(round(r - w))
                if n - 2 * a > 0:
                    res[j] = (a, n - a)
        return res
    outer, inner, lifted = spans(r, 0), spans(r - 1, 0), spans(r - 1, -1)
    for j, (a, b) in outer.items():
        out.append((x + a * P, y + j * P, (b - a) * P, P, 'line', 1.0))
    for j, (a, b) in inner.items():
        out.append((x + a * P, y + j * P, (b - a) * P, P, sh, 1.0))      # shade: the bottom rim
        if j in lifted:                                                   # fill: the inner disc
            la, lb = lifted[j]                                            # lifted one pixel,
            a2, b2 = max(a, la), min(b, lb)                               # kept inside the line
            if b2 > a2:
                out.append((x + a2 * P, y + j * P, (b2 - a2) * P, P, fill, 1.0))
    if danger:
        out.append((x + (n // 2 - 1) * P, y + (n - 2) * P, 2 * P, P, 'red', 1.0))
    return out


def icon(name, x, y, P, color):
    g = ICONS.get(name)
    if not g:
        return []
    x, y = int(round(x / P)) * P, int(round(y / P)) * P
    r = []
    for j, row in enumerate(g):
        i = 0
        while i < len(row):
            if row[i] == '#':
                k = i
                while k < len(row) and row[k] == '#':
                    k += 1
                r.append((x + i * P, y + j * P, (k - i) * P, P, color, 1.0))
                i = k
            else:
                i += 1
    return r


def chip(x, y, w, h, P):
    """outline of a color swatch (the swatch itself is drawn by the renderer)"""
    x, y = int(round(x / P)) * P, int(round(y / P)) * P
    w, h = int(round(w / P)) * P, int(round(h / P)) * P
    return [(x + P, y, w - 2 * P, P, 'line', 1.0), (x + P, y + h - P, w - 2 * P, P, 'line', 1.0),
            (x, y + P, P, h - 2 * P, 'line', 1.0), (x + w - P, y + P, P, h - 2 * P, 'line', 1.0)]


def expand(op, P):
    """rects for a UI op, or None if the op is not a pure-rect piece"""
    k = op[0]
    if k == 'panel':
        _, x, y, w, h, style = op[:6]
        return panel(x, y, w, h, style, P)
    if k == 'tail':
        _, x, y, d = op[:4]
        return tail(x, y, d, P)
    if k == 'switch':
        _, x, y, on = op
        return switch(x, y, on, P)
    if k == 'bar':
        _, x, y, w, frac = op
        return bar(x, y, w, frac, P)
    if k == 'circle':
        _, x, y, d = op[:4]
        hot = op[4] if len(op) > 4 else False
        return circle(x, y, d, P, hot)
    if k == 'icon':
        _, name, x, y, color = op[:5]
        mult = op[5] if len(op) > 5 else 1
        return icon(name, x, y, P * mult, color)
    return None

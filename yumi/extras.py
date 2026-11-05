"""The small things that make her feel alive, as a mixin for Brain (pure Python, no GTK):

  eyes that follow the pointer, being petted, dozing off when you are away, health breaks,
  the first-run tour, seasonal outfits (overlays and fliers), her own round buttons.

Brain calls extras_init / extras_motion / extras_update / extras_ops / tour_ops and handles the
actions 'custom', 'tour_next', 'tour_skip', 'tour_again' through extras_do.
"""
import math
import random

from .config import RING_SLOTS, load_actions, tr
from .pixui import ICONS

PET_WINDOW = 1.6        # s: strokes counted within this long
PET_TRAVEL = 60        # pointer travel over her head (sprite px * S) in the window...
PET_TURNS = 2           # ...with at least this many back-and-forth turns
PET_RUN = 8             # px a stroke must go one way before turning back counts
SPARK_LIFE = 0.8
BREAK_KINDS = ('water', 'eyes', 'stretch')
TOUR_STEPS = 5
# id: action, arg, icon, label; the settings button can move but not go: nothing else opens them
RING_BUILTIN = {'recent': ('pile', 'recent', 'box', 'recent'), 'shelf': ('pile', 'shelf', 'shelf', 'shelf'),
                'pipette': ('pipette', None, 'drop', 'pipette'), 'timer': ('timer', None, 'clock', 'timer'),
                'sleep': ('sleep', None, 'dnd', 'sleep'), 'settings': ('menu', 'settings', 'gear', 'settings')}
RING_ALPHA = 0.55
RING_ALPHA_OFF = 0.22
FORM_NAME_MAX = 18


def inside_rect(r, p):
    return r[0] <= p[0] <= r[0] + r[2] and r[1] <= p[1] <= r[1] + r[3]


class Extras:
    # ---------------------------------------------------------------- setup
    def extras_init(self, now):
        self.gaze = 0                    # -1 looks to the viewer's left, 1 right
        self.gaze_want, self.gaze_since = 0, now
        self.pet_acc, self.pet_until, self.pet_last = 0.0, 0.0, -99.0
        self.pet_pt, self.hearts, self.next_heart = None, [], 0.0
        self.pet_moves, self.pet_turns = [], []     # (time, travel) and turn times, recent ones
        self.pet_dir, self.pet_run = 0, 0.0
        self.sparks, self.spark_at = [], None
        self.nap = False
        self.user_idle = False
        self.break_next, self.break_i = None, 0
        self.tour = None
        self.custom = load_actions()
        self.ring_edit = False           # the button editor is open on the ring
        self.ring_drag = None            # id of the button pressed there...
        self.ring_moved = False          # ...and whether it has been pulled away yet
        self.ring_slots = []             # centers of the ring's places, refreshed every scene()
        self.form = None                 # the new / edit button form: see form_open

    # ---------------------------------------------------------------- eyes, petting
    def head_rect(self):
        pose, _i, _k, _ix, _iy, bx, by, _w, _h = self.origin()
        if pose != 'stand':
            return None
        S = self.S
        return (bx + 24 * S, by + 4 * S, 72 * S, 62 * S)

    def can_pet(self):
        return (self.mode == 'docked' and self.out and not self.sleeping and not self.press
                and not self.menu and not self.pile and not self.file_drag)

    def extras_motion(self, x, y):
        """petting is the pointer going back and forth over her head (either way, any speed)"""
        r = self.head_rect() if self.can_pet() else None
        now = self.now
        if r and r[0] <= x <= r[0] + r[2] and r[1] <= y <= r[1] + r[3]:
            if self.pet_pt:
                dx, dy = x - self.pet_pt[0], y - self.pet_pt[1]
                self.pet_moves.append((now, abs(dx) + 0.5 * abs(dy)))
                d = 1 if dx > 0 else -1 if dx < 0 else 0
                if d:
                    if d == self.pet_dir:
                        self.pet_run += abs(dx)
                    else:
                        if self.pet_dir and self.pet_run >= PET_RUN * self.S:
                            self.pet_turns.append(now)
                        self.pet_dir, self.pet_run = d, abs(dx)
            self.pet_pt = (x, y)
            if now < self.pet_until:
                self.spark_trail(x, y, now)
        else:
            self.pet_pt = None
            self.pet_dir, self.pet_run = 0, 0.0

    def spark_trail(self, x, y, now):
        """little sparkles that fall off the pointer while she is being petted"""
        S = self.S
        last = self.spark_at
        if last and math.hypot(x - last[0], y - last[1]) < 5 * S and now - last[2] < 0.035:
            return
        self.spark_at = (x, y, now)
        for _ in range(1 if last else 2):
            self.sparks.append({'x': x + random.uniform(-12, 4) * S, 'y': y + random.uniform(-10, 2) * S,
                                't': now, 'vx': random.uniform(-14, 14) * S,
                                'vy': random.uniform(-26, -8) * S,
                                'kind': random.choice(('dot', 'dot', 'cross', 'heart')),
                                'col': random.choice(('heart', 'heart', 'paper', 'acc_hi'))})
        del self.sparks[:-40]

    def extras_update(self, dt, now):
        S = self.S
        # eyes: look toward the pointer, a little lazily
        want = 0
        if self.pointer and self.settings.get('gaze', True) and self.visible() and not self.sleeping:
            r = self.head_rect()
            if r:
                dx = self.pointer[0] - (r[0] + r[2] / 2)
                want = -1 if dx < -70 * S else 1 if dx > 70 * S else 0
        if want != self.gaze_want:
            self.gaze_want, self.gaze_since = want, now
        elif want != self.gaze and now - self.gaze_since > 0.18:
            self.gaze = want
        # petting: enough back-and-forth over her head lately
        self.pet_moves = [m for m in self.pet_moves if now - m[0] < PET_WINDOW]
        self.pet_turns = [t for t in self.pet_turns if now - t < PET_WINDOW]
        self.pet_acc = sum(m[1] for m in self.pet_moves)
        petting = now < self.pet_until
        if self.can_pet() and (
                self.pet_acc >= PET_TRAVEL * S and len(self.pet_turns) >= PET_TURNS
                or petting and self.pet_acc >= 0.35 * PET_TRAVEL * S and self.pet_turns):
            if not petting and now - self.pet_last > 12:
                self.pet_last = now
                self.say('pet')
            self.pet_until = max(self.pet_until, now + 1.2)
            if not petting or self.face not in ('shy', 'happy'):
                self.set_face('shy' if random.random() < 0.5 else 'happy', 1.8)
            else:
                self.face_until = max(self.face_until, now + 0.6)
        self.sparks = [p for p in self.sparks if now - p['t'] < SPARK_LIFE]
        if now < self.pet_until and now >= self.next_heart:
            r = self.head_rect()
            if r:
                self.next_heart = now + 0.32
                self.hearts.append({'x': r[0] + r[2] / 2 + random.uniform(-44, 44) * S, 'y': r[1] + 6 * S,
                                    't': now, 'vx': random.uniform(-6, 6) * S})
        self.hearts = [h for h in self.hearts if now - h['t'] < 1.5]
        # health breaks wait for you: not while she dozes, nor while you are away
        mins = self.settings.get('breaks') or 0
        if not mins:
            self.break_next = None
        else:
            if self.break_next is None:
                self.break_next = now + mins * 60
            if self.sleeping or self.user_idle:
                self.break_next += dt
            elif now >= self.break_next:
                self.break_next = now + mins * 60
                self.say('break_' + BREAK_KINDS[self.break_i % 3])
                self.break_i += 1
                if self.mode == 'docked' and not self.out:
                    self.pop(True, temporary=True)
        # the first run: a tour a moment after she appears
        if not self.state.get('intro_done') and self.tour is None and now > 5 and not self.sleeping:
            self.start_tour()

    # ---------------------------------------------------------------- dozing off
    def idle_event(self, kind):
        """kind: 'idle' (a few minutes away; at night this is enough), 'idle_long', 'active'"""
        if kind == 'active':
            self.user_idle = False
            if self.nap:
                self.nap = False
                self.set_sleep(False, nap=True)
            return
        self.user_idle = True
        if not self.settings.get('idle_nap', True) or self.sleeping:
            return
        import datetime
        night = datetime.datetime.now().hour >= 23 or datetime.datetime.now().hour < 6
        if kind == 'idle_long' or night:
            self.nap = True
            self.set_sleep(True, nap=True)

    # ---------------------------------------------------------------- drawing
    def gaze_overlay(self, bx, by):
        key = {-1: 'gaze:l', 1: 'gaze:r'}.get(self.gaze)
        if key and key in self.meta.names:
            return [('spr', key, 0, bx, by, 1.0, 0.0, 0, 0)]
        return []

    def hat_ops(self, key, bx, by):
        """an outfit's hat, body-local x/y from outfit.json; her antennae poke out of its slits"""
        # always there, even without a hat: while one outfit fades into another, the window
        # draws the old outfit's hat from this op (a key with no sprite is simply skipped)
        hat = self.meta.fx.get('hat') or {}
        S = self.S
        return [('spr', key, 0, round(bx + hat.get('x', 0) * S), round(by + hat.get('y', 0) * S),
                 1.0, 0.0, 0, 0)]

    def extras_ops(self, pose, ix, iy, bx, by):
        S, now = self.S, self.now
        ops = []
        fx = self.meta.fx
        # a pumpkin (or whatever the outfit says) by her feet
        if pose == 'stand':
            for ov in fx.get('overlays', []):
                key = 'acc:' + ov['img'].rsplit('.', 1)[0]
                if key not in self.meta.names:
                    continue
                w, h = self.meta.size[key]
                foot_x, foot_y = self.foot()
                side = -1 if ov.get('side', 'left') == 'left' else 1
                x = foot_x + side * (34 * S) + ov.get('dx', 0) * S - (w * S if side < 0 else 0)
                ops.append(('spr', key, 0, round(x), round(foot_y - (h - ov.get('dy', 0)) * S), 1.0, 0.0, 0, 0))
        # fliers circling her head (bats)
        fl = fx.get('fliers')
        key = fl and 'acc:' + fl['img'].rsplit('.', 1)[0]
        if fl and key in self.meta.names and pose in ('stand', 'sit') and self.visible():
            r = self.head_rect() or (bx + 24 * S, by + 4 * S, 72 * S, 62 * S)
            cx, cy = r[0] + r[2] / 2, r[1] - 6 * S
            n = fl.get('count', 3)
            for k in range(n):
                a = now * 0.7 + k * 2 * math.pi / n
                x = cx + math.cos(a) * 92 * S - fl['fw'] * S / 2
                y = cy + math.sin(a) * 34 * S + math.sin(now * 1.3 + k) * 6 * S - 8 * S
                ops.append(('spr', key, int(now * 6 + k) % fl.get('frames', 2), round(x), round(y),
                            1.0, 0.0, 0, 0))
        # sparkles trailing the pointer
        P = self.P
        for p in self.sparks:
            age = now - p['t']
            a = max(0.0, min(1.0, 2.5 * (1.0 - age / SPARK_LIFE)))
            x = round(p['x'] + p['vx'] * age)
            y = round(p['y'] + p['vy'] * age + 30 * S * age * age)
            if p['kind'] == 'dot':
                ops.append(('rect', x, y, P, P, p['col'], a))
            elif p['kind'] == 'cross':
                ops.append(('rect', x - P, y, 3 * P, P, p['col'], a))
                ops.append(('rect', x, y - P, P, 3 * P, p['col'], a))
            else:                    # a tiny heart
                for j, row in enumerate(('.#.#.', '#####', '.###.', '..#..')):
                    i0 = row.index('#')
                    if row.count('#') == 2:
                        ops.append(('rect', x + (1 - 2) * P, y - 2 * P, P, P, 'heart', a))
                        ops.append(('rect', x + (3 - 2) * P, y - 2 * P, P, P, 'heart', a))
                    else:
                        n = row.count('#')
                        ops.append(('rect', x + (i0 - 2) * P, y + (j - 2) * P, n * P, P, 'heart', a))
        # hearts
        for h in self.hearts:
            age = now - h['t']
            x = h['x'] + h['vx'] * age
            y = h['y'] - 38 * S * age
            ops.append(('icon', 'heart', round(x), round(y), 'heart', 1))
        return ops

    # ---------------------------------------------------------------- tour
    def start_tour(self):
        self.tour = {'step': 0}
        if self.mode == 'docked':
            self.pop(True)

    def tour_geom(self):
        """(x, y, w, h, lines) of the tour panel for the current step"""
        S, P = self.S, self.P
        w = 150 * P
        lines = self.wrap(tr(self.settings, f"tour{self.tour['step'] + 1}"), w - 14 * P)
        lh = self.text_size('Ag')[1]
        bh = 13 * P
        h = 5 * P + lh + 3 * P + len(lines) * lh + 4 * P + bh + 5 * P
        hx, hy = self.head_top()
        left = hx > self.W / 2
        x = hx - w - 70 * S if left else hx + 70 * S
        y = hy - 8 * S
        x = min(max(x, 4 * P), self.W - w - 4 * P)
        y = min(max(y, 4 * P), self.H - h - 4 * P)
        return x, y, w, h, lines

    def tour_ops(self):
        S, P = self.S, self.P
        step = self.tour['step']
        t = lambda k: tr(self.settings, k)
        x, y, w, h, lines = self.tour_geom()
        lh = self.text_size('Ag')[1]
        bh = 13 * P
        ops = [('panel', x, y, w, h, 'window'),
               ('text', x + 7 * P, y + 5 * P, t('tour_title') + f'  {step + 1}/{TOUR_STEPS}', 'muted')]
        ty = y + 5 * P + lh + 3 * P
        for k, line in enumerate(lines):
            ops.append(('text', x + 7 * P, ty + k * lh, line, 'fg'))
        by = y + h - bh - 5 * P
        last = step >= TOUR_STEPS - 1
        nxt = (x + w - 7 * P - 56 * P, by, 56 * P, bh)
        labels = [(nxt, t('tour_done') if last else t('tour_next'), 'tour_next')]
        if not last:
            labels.append(((x + 7 * P, by, 48 * P, bh), t('tour_skip'), 'tour_skip'))
        for r, lbl, act in labels:
            hot = self.pointer is not None and r[0] <= self.pointer[0] <= r[0] + r[2] and \
                r[1] <= self.pointer[1] <= r[1] + r[3]
            ops.append(('panel', *r, 'button_hot' if hot else 'button'))
            tw, th = self.text_size(lbl)
            ops.append(('text', r[0] + (r[2] - tw) / 2, r[1] + (r[3] - P - th) / 2, lbl,
                        'acc_hi' if act == 'tour_next' else 'fg'))
            self.buttons.append((r, act, None))
        self.hits['tour'] = (x, y, w, h)
        return ops

    # ---------------------------------------------------------------- actions
    def extras_do(self, action, arg):
        """True when the action was one of ours"""
        if action == 'custom':
            if isinstance(arg, int) and 0 <= arg < len(self.custom):
                self.close_all()
                self.requests.append(('run', self.custom[arg]['command']))
                self.set_face('happy', 1.5)
            return True
        if action == 'ring_edit':
            self.ring_edit = True
            self.open_radial()
            return True
        if action == 'ring_done':
            self.close_radial()
            self.open_menu('settings')
            return True
        if action == 'ring_add':
            self.form_open()
            return True
        if action == 'ring_del':
            self.ring_delete(arg)
            return True
        if action == 'ring_on':
            self.ring_switch(arg)
            return True
        if action == 'form_field':
            self.form.update(field=arg, pos=len(self.form[arg]))
            return True
        if action == 'form_icon':
            self.form['icon'] = arg
            return True
        if action == 'form_pick':
            self.requests.append(('form_pick',))
            return True
        if action == 'form_cancel':
            self.form = None
            return True
        if action == 'form_save':
            self.form_save()
            return True
        if action == 'ring_tile':        # handled from the press and the release: ring_grab, ring_drop
            return True
        if action == 'tour_next':
            if self.tour:
                self.tour['step'] += 1
                if self.tour['step'] >= TOUR_STEPS:
                    self.end_tour()
            return True
        if action == 'tour_skip':
            self.end_tour()
            return True
        if action == 'tour_again':
            self.menu = None
            self.close_radial()
            self.start_tour()
            return True
        return False

    def end_tour(self):
        self.tour = None
        self.state['intro_done'] = True
        self.requests.append(('save',))
        self.set_face('happy', 2.0)

    def custom_items(self):
        ru = self.settings.get('ui_lang') == 'ru'
        return [('custom', i, a['icon'] if a['icon'] in ICONS or a['icon'].startswith('/') and self.exists(a['icon'])
                 else 'star', a['label_ru'] if ru else a['label'], 'cmd:' + a['command'])
                for i, a in enumerate(self.custom)]

    # ---------------------------------------------------------------- her round buttons
    def ring_table(self):
        """id: (action, arg, icon, label, id) of every button, the settings one last"""
        t = lambda k: tr(self.settings, k)
        out = {i: (a, g, ic, t(lbl), i) for i, (a, g, ic, lbl) in RING_BUILTIN.items() if i != 'settings'}
        if self.sleeping:
            out['sleep'] = ('wake', None, 'dnd', t('wake'), 'sleep')
        out.update((it[4], it) for it in self.custom_items())
        a, g, ic, lbl = RING_BUILTIN['settings']
        out['settings'] = (a, g, ic, t(lbl), 'settings')
        return out

    def ring_ids(self, table=None):
        table = table or self.ring_table()
        saved = self.settings.get('ring') or []
        return [i for i in saved if i in table] + [i for i in table if i not in saved]

    def ring_shown(self, i):
        # only the built-in ones switch off; asleep, the wake button stays: it is the way back
        off = i in (self.settings.get('ring_off') or []) and i in RING_BUILTIN and i != 'settings'
        return not off or (i == 'sleep' and self.sleeping)

    def radial_items(self):
        table = self.ring_table()
        return [table[i] for i in self.ring_ids(table) if self.ring_shown(i)]

    def ring_switch(self, i):
        off = list(self.settings.get('ring_off') or [])
        if i in off:
            off.remove(i)
        else:
            off.append(i)
        self.settings['ring_off'] = off
        self.requests.append(('save_settings',))

    def ring_delete(self, i):
        self.custom = [a for a in self.custom if 'cmd:' + a['command'] != i]
        self.requests.append(('save_actions',))

    def ring_add(self, command, label, icon, old=None):
        """from the button window: `old` is the command of the button being edited, which keeps
        its place; a command that another button already has replaces that button"""
        prev = next((a for a in self.custom if a['command'] == old), None)
        entry = dict(prev or {})
        entry.update({'icon': icon, 'command': command})
        if entry.get('label') == entry.get('label_ru'):              # one name for both languages
            entry.update(label=label, label_ru=label)
        else:                                                        # the other language's name stays
            entry['label_ru' if self.settings.get('ui_lang') == 'ru' else 'label'] = label
        at = self.custom.index(prev) if prev else None
        if prev:
            self.custom[at] = None
        self.custom = [a for a in self.custom if a and a['command'] != command]
        if prev:
            self.custom.insert(min(at, len(self.custom)), entry)
            ring = self.settings.get('ring') or []
            if 'cmd:' + command not in ring:
                self.settings['ring'] = ['cmd:' + command if i == 'cmd:' + old else i for i in ring]
                self.requests.append(('save_settings',))
        else:
            self.custom.append(entry)
        self.requests.append(('save_actions',))

    def ring_grab(self, on, button):
        if button == 1 and on and on.startswith('btn:') and int(on[4:]) < len(self.buttons):
            _r, action, arg = self.buttons[int(on[4:])]
            if action == 'ring_tile':
                self.ring_drag = arg
                self.ring_moved = False

    def ring_drag_to(self, x, y):
        """the others make way as soon as the pointer is nearer another place; free places
        stay at the end, like the empty tiles in Android's editor"""
        p = self.press
        if not self.ring_moved and p and math.hypot(x - p['x'], y - p['y']) < 6 * self.S:
            return                               # still a click
        self.ring_moved = True
        ids = self.ring_ids()
        if self.ring_drag not in ids or len(self.ring_slots) < len(ids):
            return
        k = min(range(len(ids)), key=lambda n: math.hypot(self.ring_slots[n][0] - x, self.ring_slots[n][1] - y))
        ids.remove(self.ring_drag)
        ids.insert(k, self.ring_drag)
        self.settings['ring'] = ids

    def ring_drop(self):
        i, self.ring_drag = self.ring_drag, None
        if self.ring_moved:
            self.requests.append(('save_settings',))
        elif i.startswith('cmd:'):               # a click on your own button: edit it
            self.form_open(i[4:])

    def ring_icon_op(self, icon, px, py, d, color):
        P = self.P
        if icon.startswith('/'):
            return ('thumb', icon, px + 3 * P, py + 3 * P, d - 6 * P, d - 6 * P)
        return ('icon', icon, px + (d - 7 * P) / 2, py + (d - 7 * P) / 2 - P / 2, color)

    def ring_tile_ops(self, item, px, py, d):
        """one place on the ring in the editor: a see-through button in a white outline with a
        switch (built-in) or a minus (yours) on it, or a hollow circle with a plus"""
        P = self.P
        circ = (px, py, d, d)
        hot = self.pointer is not None and inside_rect(circ, self.pointer)
        if item is None:
            self.buttons.append((circ, 'ring_add', None))
            return [('hollow', px, py, d),
                    ('icon', 'plus', px + (d - 7 * P) / 2, py + (d - 7 * P) / 2, 'acc_hi' if hot else 'white')]
        _a, _g, icon, label, i = item
        on = i not in (self.settings.get('ring_off') or []) or i not in RING_BUILTIN
        ops = [('group', RING_ALPHA if on else RING_ALPHA_OFF,
                [('circle', px, py, d, hot), self.ring_icon_op(icon, px, py, d, 'fg')]),
               ('hollow', px, py, d)]
        tw, _th = self.text_size(label)
        lx, ly = px + d / 2 - tw / 2, py + d + P
        ops += [('text', lx + P, ly + P, label, 'shadow'), ('text', lx, ly, label, 'white' if on else 'muted')]
        if not (i == self.ring_drag and self.ring_moved):      # first in the list: hit before the tile
            if i.startswith('cmd:'):
                b = 9 * P
                mx, my = px + d - b + P, py - P
                minus = (mx - P, my - P, b + 2 * P, b + 2 * P)
                over = self.pointer is not None and inside_rect(minus, self.pointer)
                ops += [('circle', mx, my, b, over), ('rect', mx + 2 * P, my + 4 * P, 5 * P, P, 'white', 1.0)]
                self.buttons.append((minus, 'ring_del', i))
            elif i != 'settings':
                sx, sy = px + d - 11 * P, py - 3 * P
                ops.append(('switch', sx, sy, on))
                self.buttons.append(((sx - P, sy - P, 16 * P, 9 * P), 'ring_on', i))
        self.buttons.append((circ, 'ring_tile', i))
        return ops

    # ---------------------------------------------------------------- the button form
    def form_open(self, command=None):
        """a new button, or with `command` the one that runs it"""
        old = next((a for a in self.custom if a['command'] == command), None) or {}
        icon = old.get('icon', 'star')
        cmd = old.get('command', '')
        self.form = {'old': command, 'cmd': cmd, 'pos': len(cmd), 'field': 'cmd',
                     'name': old.get('label_ru' if self.settings.get('ui_lang') == 'ru' else 'label', ''),
                     'icon': icon if icon in ICONS or icon.startswith('/') and self.exists(icon) else 'star'}

    def form_can_save(self):
        return bool(self.form['cmd'].strip() and self.form['name'].strip())

    def form_save(self):
        f = self.form
        if self.form_can_save():
            self.ring_add(f['cmd'].strip(), f['name'].strip(), f['icon'], old=f['old'])
            self.form = None

    def form_text(self, text):
        """typed or pasted at the caret"""
        f = self.form
        text = ' '.join(text.splitlines())
        val = f[f['field']]
        if f['field'] == 'name':
            text = text[:max(0, FORM_NAME_MAX - len(val))]
        f[f['field']] = val[:f['pos']] + text + val[f['pos']:]
        f['pos'] += len(text)

    def form_key(self, name, text):
        """all keys go here while it is open: Tab, Enter, Esc, editing keys, letters"""
        f = self.form
        val, pos = f[f['field']], f['pos']
        if name == 'Escape':
            self.form = None
        elif name in ('Return', 'KP_Enter'):
            self.form_save()
        elif name in ('Tab', 'ISO_Left_Tab'):
            field = 'name' if f['field'] == 'cmd' else 'cmd'
            f.update(field=field, pos=len(f[field]))
        elif name == 'BackSpace':
            if pos:
                f[f['field']] = val[:pos - 1] + val[pos:]
                f['pos'] = pos - 1
        elif name == 'Delete':
            f[f['field']] = val[:pos] + val[pos + 1:]
        elif name == 'Left':
            f['pos'] = max(0, pos - 1)
        elif name == 'Right':
            f['pos'] = min(len(val), pos + 1)
        elif name == 'Home':
            f['pos'] = 0
        elif name == 'End':
            f['pos'] = len(val)
        elif text:
            self.form_text(text)
        return True

    def form_field_ops(self, field, x, y, w):
        """a text line, scrolled so that the caret is always in it"""
        P = self.P
        f = self.form
        h = 13 * P
        r = (x, y, w, h)
        active = f['field'] == field
        val = f[field]
        room = w - 8 * P
        ops = [('panel', *r, 'inset')]
        pos = f['pos'] if active else len(val)
        start = 0
        while start < pos and self.text_size(val[start:pos])[0] > room:
            start += 1
        shown = val[start:]
        while shown and self.text_size(shown)[0] > room:
            shown = shown[:-1]
        tx, ty = x + 4 * P, y + (h - self.text_size('Ag')[1]) / 2
        if shown:
            ops.append(('text', tx, ty, shown, 'fg'))
        if active and int(self.now * 2) % 2 == 0:
            before = val[start:pos]
            cx = tx + (self.text_size(before)[0] - P if before else 0)
            ops.append(('rect', cx, y + 2 * P, P, 9 * P, 'acc_hi', 1.0))
        self.buttons.append((r, 'form_field', field))
        return ops

    def form_ops(self):
        """pieces of her other menus: panel, fields, a grid of icons, buttons; it takes every click"""
        P = self.P
        t = lambda k: tr(self.settings, k)
        f = self.form
        self.buttons = []
        names = list(ICONS)
        cols, cell = 12, 11 * P
        rows = (len(names) + cols - 1) // cols
        lh = self.text_size('Ag')[1]
        bh = 15 * P
        w = cols * cell + 14 * P
        h = 5 * P + lh + 2 * P + 2 * (lh + 13 * P + 2 * P) + lh + rows * cell + 3 * P + bh + 3 * P + bh + 5 * P
        ring = self.hits.get('radial')
        if ring:
            x = ring[0] - w - 4 * P if self.ring_left else ring[0] + ring[2] + 4 * P
            y = ring[1]
        else:
            x, y = self.menu_pos(w, h)
        x = min(max(x, 4 * P), self.W - w - 4 * P)
        y = min(max(y, 4 * P), self.H - h - 4 * P)
        inner = w - 14 * P
        lx = x + 7 * P
        ops = [('panel', x, y, w, h, 'window'),
               ('text', lx, y + 5 * P, t('ring_change' if f['old'] else 'ring_new'), 'muted')]
        self.hits['form'] = (x, y, w, h)
        ry = y + 5 * P + lh + 2 * P
        for field, key in (('cmd', 'ring_cmd'), ('name', 'ring_name')):
            ops.append(('text', lx, ry, t(key), 'acc_hi' if f['field'] == field else 'fg'))
            ry += lh
            ops += self.form_field_ops(field, lx, ry, inner)
            ry += 13 * P + 2 * P
        ops.append(('text', lx, ry, t('ring_icon'), 'fg'))
        ry += lh
        for k, name in enumerate(names):
            r = (lx + (k % cols) * cell, ry + (k // cols) * cell, cell, cell)
            hot = self.pointer is not None and inside_rect(r, self.pointer)
            if name == f['icon']:
                ops.append(('panel', *r, 'tab_on'))
            elif hot:
                ops.append(('panel', *r, 'row_hot'))
            ops.append(('icon', name, r[0] + 2 * P, r[1] + 2 * P, 'paper' if name == f['icon'] or hot else 'fg'))
            self.buttons.append((r, 'form_icon', name))
        ry += rows * cell + 3 * P

        def button(rx, label, action, rw=None, on=True):
            tw, th = self.text_size(label)
            r = (rx, ry, rw or tw + 12 * P, bh)
            hot = on and self.pointer is not None and inside_rect(r, self.pointer)
            ops.append(('panel', *r, 'button_hot' if hot else 'button' if on else 'tab'))
            ops.append(('text', r[0] + (r[2] - tw) / 2, r[1] + (r[3] - P - th) / 2, label, 'fg' if on else 'muted'))
            if on:
                self.buttons.append((r, action, None))
            return r

        r = button(lx, t('ring_file'), 'form_pick')
        if f['icon'].startswith('/'):
            ops.append(('thumb', f['icon'], r[0] + r[2] + 4 * P, ry, bh, bh))
        ry += bh + 3 * P
        half = (inner - 4 * P) / 2
        button(lx, t('cancel'), 'form_cancel', half)
        button(lx + half + 4 * P, t('ring_save' if f['old'] else 'ring_add'), 'form_save', half, self.form_can_save())
        return ops

    def ring_done_ops(self, cx, cy, R, left):
        """the way out of the editor, inside the arc: it stays open while you are elsewhere"""
        P = self.P
        label = tr(self.settings, 'ring_done')
        tw, th = self.text_size(label)
        w, h = tw + 12 * P, 15 * P
        x = cx + (-R if left else R) * 0.62 - w / 2
        x = min(max(x, 4 * P), self.W - w - 4 * P)
        y = min(max(cy - h / 2, 4 * P), self.H - h - 4 * P)
        r = (x, y, w, h)
        hot = self.pointer is not None and inside_rect(r, self.pointer)
        self.buttons.append((r, 'ring_done', None))
        return [('panel', *r, 'button_hot' if hot else 'button'),
                ('text', x + (w - tw) / 2, y + (h - P - th) / 2, label, 'fg')]

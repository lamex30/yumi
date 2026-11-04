"""Yumi's behaviour as a pure state machine (no GTK): input events in, a list of draw ops out.

The GTK window (moth_girl.py) forwards pointer/drag events and the clock, draws the ops with
cairo and carries out `requests` (open a file, run a job, toggle Do Not Disturb...). The same
ops can be drawn with PIL, which is how this file is tested without a desktop.

Screen coordinates are pixels of the fullscreen overlay. Sprites are drawn at integer `scale`.
Her position is her "foot": bottom-centre of the current frame image.
"""
import math
import random
from pathlib import Path

from . import jobs as J
from .config import UI, tr
from .extras import Extras
from .sprites import ARCHIVES, DOCS, KIND_COLOR, SWATCHES, swatch_hex

SEQ = [0, 1, 2, 3, 4, 3, 2, 1]
LONG_PRESS = 0.22           # s held on her before she is picked up
DRAG_START = 6              # px moved while pressed that also picks her up
DOUBLE = 0.26               # s to wait for a second click
HIDE_DELAY = 0.35
CLOSE_DELAY = 1.6           # pile/menu close after the pointer has been away this long
DOCK_SNAP = 40              # px from the bottom edge that counts as "she can hide here"
EDGE_MARGIN = 24
UI_PX = 2                   # one interface pixel = 2 sprite pixels: chunky, like 16-bit menus
CARD_W, CARD_H, CARD_GAP = 132, 26, 3     # in interface pixels: big, easy to grab
MAX_CARDS = 6
ROW_H = 13                  # menu row, interface pixels
SIT_SEAT = 120              # row of the sit sprite that rests on the box top
BOX_TOP = 4                 # first row of the box sprite's lid


class Spring:
    def __init__(self, x):
        self.x = float(x)
        self.v = 0.0
        self.t = float(x)
        self.w, self.z = 16.0, 0.62

    def to(self, t, w=16.0, z=0.62):
        self.t, self.w, self.z = float(t), w, z

    def snap(self, x):
        self.x = self.t = float(x)
        self.v = 0.0

    def step(self, dt):
        if abs(self.t - self.x) < 0.3 and abs(self.v) < 3:
            self.x, self.v = self.t, 0.0
            return False
        n = int(dt * 240) + 1
        h = dt / n
        k, c = self.w * self.w, 2 * self.z * self.w
        for _ in range(n):
            self.v += (k * (self.t - self.x) - c * self.v) * h
            self.x += self.v * h
        return True


def file_kind(path):
    k = J.kind_of(path)
    if k:
        return k
    e = J.ext_of(path)
    return 'doc' if e in DOCS else 'archive' if e in ARCHIVES else 'other'


def inside(r, x, y):
    return r is not None and r[0] <= x < r[0] + r[2] and r[1] <= y < r[1] + r[3]


def grow(r, d):
    return (r[0] - d, r[1] - d, r[2] + 2 * d, r[3] + 2 * d)


TYPE_CPS = 28                # letters per second while her words type out


def reveal_times(text):
    """(when each letter of `text` appears, s after she starts; the time it is all out):
    a little pause after commas, a longer one after a sentence, like someone talking"""
    t, out = 0.0, []
    for i, ch in enumerate(text):
        out.append(t)
        t += 1.0 / TYPE_CPS
        nxt = text[i + 1] if i + 1 < len(text) else ' '
        if ch in '.!?…' and nxt == ' ':
            t += 0.26
        elif ch in ',;:—' and nxt == ' ':
            t += 0.12
    return out, (out[-1] if out else 0.0) + 0.05


class Moth:
    """the tiny moth that marks where Yumi hides"""

    def __init__(self, brain):
        self.b = brain
        self.state = 'wander'
        self.x = self.y = 0.0
        self.tm = random.random() * 10
        self.land_at = 20 + random.random() * 20
        self.land_until = 0.0
        self.startle_until = 0.0
        self.placed = False
        self.under = False

    def size(self):
        S = self.b.S
        return self.b.meta.moth['w'] * S, self.b.meta.moth['h'] * S

    def rect(self):
        w, h = self.size()
        return (self.x - w / 2, self.y - h / 2, w, h)

    def home(self):
        b = self.b
        return b.dock_x, b.H - 52 * b.S

    def update(self, dt, now):
        b = self.b
        S = b.S
        hx, hy = self.home()
        if not self.placed:
            self.x, self.y, self.placed = hx, hy, True
        want = self.state
        # the moth marks where she hides, so it is only there while she is all the way under
        # the edge: as soon as any of her shows (peeking, out, asleep in her blanket, held,
        # flying, pinned) it flies back into her and is gone
        hidden = (b.mode == 'docked' and not b.out and not b.pile and not b.visible()
                  and not b.settings.get('pinned'))
        if not hidden:
            want = 'hide' if self.state not in ('gone',) else 'gone'
        elif self.state in ('gone', 'hide', 'head', 'sleep'):
            want = 'emerge'
        if want != self.state:
            if want == 'emerge':
                cx, cy = b.chest()
                self.x, self.y = cx, cy
            self.state = want
        speed = 1.0
        if b.pointer:
            d = math.hypot(b.pointer[0] - self.x, b.pointer[1] - self.y)
            speed = 0.3 + 0.7 * min(1.0, d / (160 * S))
        if b.busy or b.music:
            speed *= 1.6
        self.tm += dt * speed
        self.under = False
        if self.state == 'wander' or self.state == 'emerge':
            if self.state == 'wander' and now > self.land_at and not b.busy:
                self.state = 'land'
                self.land_until = now + 6 + random.random() * 6
            t = self.tm
            tx = hx + S * (34 * math.sin(0.9 * t) + 12 * math.sin(2.3 * t))
            ty = hy - S * (14 * math.sin(1.3 * t) + 8 * math.cos(0.7 * t))
            k = min(1.0, dt * (2.5 if self.state == 'wander' else 3.5))
            self.x += (tx - self.x) * k
            self.y += (ty - self.y) * k
            if self.state == 'emerge' and math.hypot(tx - self.x, ty - self.y) < 6 * S:
                self.state = 'wander'
            self.under = self.state == 'emerge'
        elif self.state == 'land':
            tx, ty = hx + 26 * S, b.H - self.size()[1] / 2 + 3 * S
            k = min(1.0, dt * 3)
            self.x += (tx - self.x) * k
            self.y += (ty - self.y) * k
            if now > self.land_until:
                self.state = 'wander'
                self.land_at = now + 25 + random.random() * 25
        elif self.state == 'hide':
            cx, cy = b.chest()
            k = min(1.0, dt * 6)
            self.x += (cx - self.x) * k
            self.y += (cy - self.y) * k
            self.under = True
            if math.hypot(cx - self.x, cy - self.y) < 8 * S:
                self.state = 'gone'
        elif self.state == 'head':
            hx2, hy2 = b.head_top()
            k = min(1.0, dt * 8)
            self.x += (hx2 - self.x) * k
            self.y += (hy2 - self.size()[1] * 0.3 - self.y) * k
        elif self.state == 'sleep':
            tx, ty = hx - 70 * S, b.H - self.size()[1] / 2 + 3 * S
            k = min(1.0, dt * 3)
            self.x += (tx - self.x) * k
            self.y += (ty - self.y) * k

    def visible(self):
        return self.state != 'gone'

    def frame(self, now):
        names = self.b.meta.moth['frames']
        if now < self.startle_until:
            return names.index('startle')
        if self.state in ('land', 'head', 'sleep'):
            per = 1.4 if self.state == 'sleep' else 0.6
            return names.index('rest0') + int(now / per) % 2
        fps = 16 if (self.b.busy or self.b.music) else 10
        return [0, 1, 2, 1][int(now * fps) % 4]


class Brain(Extras):
    def __init__(self, meta, settings, state, speech, measure, scale=1, now=0.0,
                 recent=lambda n: [], exists=lambda p: Path(p).exists()):
        self.meta = meta
        self.settings = settings
        self.state = state
        self.speech = speech
        self.measure = measure
        self.recent = recent
        self.exists = exists
        self.S = max(1, int(scale))
        self.P = UI_PX * self.S          # one interface pixel on screen
        self.swing = Spring(0)          # held: legs trail behind the motion, then swing back
        self.pile_cx = 0.0
        self.W, self.H = 1920, 1080
        self.now = now
        self.requests = []
        self.mode = state.get('mode') if state.get('mode') in ('docked', 'air') else 'docked'
        self.dock_x = 0.0
        self.x = Spring(0)
        self.y = Spring(0)
        self.yoff = Spring(0)           # docked: how far she has sunk below the edge
        self.out = False
        self.stay = False
        self.sleeping = False
        self.music = False
        self.busy = False
        self.progress = None            # (fraction, label)
        self.pile = None                # 'recent' / 'shelf'
        self.pile_t = Spring(0)
        self.pull = {}                  # card -> 1 while hovered (target)
        self.pull_v = {}                # card -> eased slide-out 0..1
        self.info = {}                  # card -> file size, read once per opening
        self.menu = None                # dict(kind, rows, x, y, w, h, data)
        self.bubble = None              # dict(text, until, face, x, y, w, h)
        self.face = None
        self.face_until = 0.0
        self.talk_until = 0.0
        self.bubble_n = 0
        self.blink_until = 0.0
        self.next_blink = now + 3
        self.pointer = None
        self.press = None
        self.click_at = None
        self.click_on = None
        self.hide_at = None
        self.away_since = None
        self.file_drag = False
        self.dnd_over = False
        self.pipette = False
        self.unavailable = set()        # settings this desktop cannot do (shown as not available yet)
        self.held_v = 0.0
        self.last_ptr = None
        self.drops = []
        self.next_chatter_check = now + 30
        self.moth = Moth(self)
        self.radial = False             # the ring of round buttons around her
        self.radial_t = Spring(0)
        self.timer = None               # see timer_* below
        self.kb_hover = False           # pointer over the timer: keyboard goes to her
        self.bubble_under = False       # the bubble had to go under the ring buttons
        self.ring_left = True           # which side of her the ring opened on
        self.hits = {}                   # name -> rect, refreshed every scene()
        self.cards = []
        self.buttons = []                # (rect, action, arg)
        self.extras_init(now)
        self.resize(self.W, self.H, first=True)

    # ------------------------------------------------------------------ geometry
    def resize(self, w, h, first=False):
        self.W, self.H = int(w), int(h)
        S = self.S
        default_x = self.W - EDGE_MARGIN * S - self.meta.bw * S / 2
        dx = self.state.get('dock_x')
        self.dock_x = float(dx) if isinstance(dx, (int, float)) else default_x
        self.dock_x = self.clamp_x(self.dock_x)
        if first:
            if self.mode == 'air' and self.state.get('air'):
                ax, ay = self.state['air']
                self.x.snap(self.clamp_x(ax))
                self.y.snap(min(max(ay, 260 * S), self.H - DOCK_SNAP * S - 1))
            else:
                self.mode = 'docked'
                self.x.snap(self.dock_x)
                self.y.snap(self.H)
            self.yoff.snap(self.hidden_off())
        elif self.mode == 'docked':
            self.x.to(self.dock_x)
            self.y.to(self.H)

    def clamp_x(self, x):
        half = self.meta.bw * self.S / 2 + 8 * self.S
        return min(max(x, half), self.W - half)

    def hidden_off(self):
        if self.sleeping:
            return self.meta.size['sleep:0'][1] * self.S * 0.5
        return (self.meta.bh + 34) * self.S

    def pose(self):
        """(pose, frame index) for now; 'stand' is the rig"""
        t = self.now
        if self.mode == 'held':
            # frame 0 has her legs to the viewer's left, frame 2 to the right. Dragged to the
            # right, her legs lag behind (left); let go of the motion and she swings back past
            # the middle a few times, like a pendulum.
            s = self.swing.x
            return 'held', 0 if s < -0.3 else 2 if s > 0.3 else 1
        if self.pile:
            return 'sit', [0, 1, 2, 1][int(t * 4) % 4]
        if self.sleeping and self.mode == 'docked':
            return 'sleep', min(2, int(max(0.0, t - self.sleep_since) / 1.2))
        if self.mode == 'air':
            p = 'hover' if self.menu else 'shy'
            return p, [0, 1, 2, 1][int(t * 4) % 4]
        return 'stand', 0

    def frame_geom(self, pose, i):
        if pose == 'stand':
            return 'body', 0, 0, self.meta.bw, self.meta.bh
        return self.meta.frame(pose, i)

    def foot(self):
        """screen position of her foot for the current pose"""
        x, y = self.x.x, self.y.x
        if self.mode == 'docked':
            y += self.yoff.x
        if self.mode == 'air' and self.pose()[0] in ('hover', 'shy'):
            y += round(2 * math.sin(self.now * 2.4)) * self.S
        if self.music and self.mode == 'docked' and not self.sleeping:
            x += round(1.5 * math.sin(self.now * 2 * math.pi * 0.9)) * self.S
            y -= round(abs(math.sin(self.now * 2 * math.pi * 0.9))) * self.S
        return x, y

    def origin(self):
        """(pose, frame, key, image x, image y, body-origin x, body-origin y, w, h)"""
        S = self.S
        pose, i = self.pose()
        key, dx, dy, w, h = self.frame_geom(pose, i)
        if pose == 'held':
            px, py = self.held_ptr
            cx, cy = self.meta.crown
            bx, by = px - cx * S, py - cy * S + 3 * S
        else:
            fx, fy = self.foot()
            bx = fx - S * (-dx + w / 2.0)
            by = fy - S * (-dy + h)
        bx, by = round(bx), round(by)
        return pose, i, key, bx - dx * S, by - dy * S, bx, by, w, h

    def her_rect(self):
        _p, _i, _k, ix, iy, _bx, _by, w, h = self.origin()
        return (ix, iy, w * self.S, h * self.S)

    def chest(self):
        _p, _i, _k, _ix, _iy, bx, by, _w, _h = self.origin()
        return bx + self.meta.bw * self.S / 2, by + 110 * self.S

    def head_top(self):
        _p, _i, _k, _ix, _iy, bx, by, _w, _h = self.origin()
        return bx + self.meta.bw * self.S / 2 + 6 * self.S, by + 2 * self.S

    def visible(self):
        if self.sleeping:
            return True                      # peeks out of the bottom edge in her blanket
        return self.mode != 'docked' or self.out or self.yoff.x < self.hidden_off() - 2

    # ------------------------------------------------------------------ speech & faces
    def say(self, event, **fmt):
        line = self.speech.say(event, out=self.visible(), asleep=self.sleeping, **fmt)
        if not line:
            return False
        text, face = line
        text = ' '.join(text.split())
        times, typed = reveal_times(text)
        dur = typed + 2.2 + 0.04 * len(text)
        self.bubble_n += 1
        self.bubble = {'text': text, 'until': self.now + dur, 'start': self.now, 'times': times,
                       'id': self.bubble_n}
        self.talk_until = self.now + typed
        vol = self.settings.get('voice_vol', 50)
        if vol and not self.sleeping:
            blips = [t for t, ch in zip(times, text) if ch.isalnum()]
            self.requests.append(('voice', blips, self.bubble_n,
                                  1 if text.rstrip().endswith('?') else 0, vol / 100.0))
        if face:
            self.set_face(face, dur)
        if self.mode == 'docked' and not self.out and not self.sleeping:
            self.pop(True, temporary=True)
        return True

    def say_sample(self):
        """a few words in her voice, so the volume can be heard while it is changed"""
        self.bubble = None
        if not self.say('voice_test'):
            self.say('hello')

    def set_face(self, face, dur=2.0):
        self.face = face
        self.face_until = self.now + dur

    def current_face(self):
        if self.now < self.talk_until:
            return ['talkA', 'talkO', None, 'talkA'][int(self.now * 8) % 4]
        if self.face and self.now < self.face_until:
            return self.face
        if self.now < self.blink_until:
            return 'blink'
        return None

    # ------------------------------------------------------------------ showing / hiding
    def pop(self, out, temporary=False):
        if self.mode != 'docked':
            return
        if out:
            self.hide_at = None
            if not self.out:
                self.out = True
                self.yoff.to(0, 16.0, 0.62)
            if not temporary:
                pass
        else:
            self.out = False
            self.stay = False
            self.yoff.to(self.hidden_off(), 22.0, 1.0)

    def wants_out(self):
        if self.mode != 'docked':
            return True
        if self.pile or self.menu or self.radial or self.file_drag or self.dnd_over or self.stay \
                or self.settings.get('pinned') or self.tour \
                or self.pipette or self.kb_hover or self.timer and self.timer.get('pending'):
            return True
        if self.bubble and self.now < self.bubble['until']:
            return True
        if self.press:
            return True
        return self.pointer is not None and self.over_anything(*self.pointer)

    def over_anything(self, x, y):
        return any(inside(r, x, y) for r in self.hits.values())

    def toggle(self):
        """Super+Z"""
        if self.sleeping:
            self.requests.append(('dnd', False))
            return
        if self.mode != 'docked':
            self.go_home()
            return
        if self.out:
            self.close_all()
            if self.settings.get('pinned'):        # the hotkey also lets go of the pin
                self.settings['pinned'] = False
                self.requests.append(('save_settings',))
            self.pop(False)
        else:
            self.pop(True)
            self.stay = True

    monitors = 1                      # how many screens there are (set by the app)

    def cycles(self):
        """settings that step through a short list when clicked"""
        return {'ui_lang': ['en', 'ru'], 'speech_lang': ['en', 'ru'],
                'breaks': [0, 20, 30, 45, 60], 'outfit': ['auto', 'off'] + self.outfit_names,
                'voice_vol': [0, 25, 50, 75, 100]}

    outfit_names = []                 # outfit folders found in sprites/outfits (set by the app)

    def cycle_text(self, arg, val):
        t = lambda k: tr(self.settings, k)
        if arg in ('ui_lang', 'speech_lang'):
            return (val or 'en').upper()
        if arg == 'breaks':
            return t('off') if not val else f'{val} {t("min")}'
        if arg == 'voice_vol':
            return t('off') if not val else f'{val}%'
        if arg == 'outfit':
            return t('o_' + str(val)) if f'o_{val}' in UI['en'] else str(val).title()
        return str(val)

    def toggle_pin(self):
        """middle click on her: stay out / hide again (also a row in the settings)"""
        if self.sleeping:
            return
        on = not self.settings.get('pinned')
        self.settings['pinned'] = on
        self.requests.append(('save_settings',))
        if on:
            self.stay = False
            self.pop(True)
        self.say('pin_on' if on else 'pin_off')

    def go_home(self):
        self.close_all()
        self.mode = 'docked'
        self.state['mode'] = 'docked'
        self.dock_x = self.clamp_x(self.W - EDGE_MARGIN * self.S - self.meta.bw * self.S / 2)
        self.state['dock_x'] = None
        self.x.to(self.dock_x, 9.0, 0.8)
        self.y.to(self.H, 9.0, 0.8)
        self.yoff.snap(0)
        self.out = True
        self.stay = False
        self.requests.append(('save',))
        self.say('home')

    def close_all(self):
        if self.pile:
            self.leave_box()
        self.pile = None
        self.menu = None
        self.close_radial()
        self.pile_t.to(0, 18.0, 1.0)

    def leave_box(self):
        """she hops off the box back to where she was"""
        self.pile = None
        self.pile_t.to(0, 18.0, 1.0)
        if self.mode == 'docked':
            self.x.to(self.dock_x, 12.0, 0.75)
            self.y.to(self.H, 12.0, 0.75)
        elif self.mode == 'air' and self.state.get('air'):
            ax, ay = self.state['air']
            self.x.to(ax, 10.0, 0.7)
            self.y.to(ay, 10.0, 0.7)

    # ------------------------------------------------------------------ sleep / world
    def set_sleep(self, on, nap=False):
        """on: asleep (Do Not Disturb) or, with nap=True, just dozing because you are away"""
        if on and not nap and self.nap:
            self.nap = False                  # Do Not Disturb takes over from the nap
        if not on and not nap and self.nap:
            return                            # a stale DND-off must not wake a napping Yumi
        if on == self.sleeping:
            return
        if on:
            self.close_all()
            if self.mode != 'docked':
                self.mode = 'docked'
                self.x.to(self.dock_x, 9.0, 0.8)
                self.y.to(self.H, 9.0, 0.8)
            self.say('nap' if nap else 'sleep')
            self.sleeping = True
            self.sleep_since = self.now
            self.out = False
            self.yoff.to(self.hidden_off(), 10.0, 1.0)
        else:
            self.sleeping = False
            self.yoff.to(0, 16.0, 0.62)
            self.out = True
            self.say('nap_wake' if nap else 'wake')

    sleep_since = 0.0

    def world(self, event, **kw):
        """system events: charger_on/off, battery_low, wifi_on/off, download, screenshot, music"""
        if event == 'music':
            playing = kw.get('playing', False)
            if playing and not self.music:
                self.say('music')
            self.music = playing
            return
        self.say(event, **kw)

    # ------------------------------------------------------------------ pointer
    def motion(self, x, y):
        self.pointer = (x, y)
        if self.mode == 'held':
            if self.last_ptr:
                dt = max(1e-3, self.now - self.last_ptr[2])
                v = (x - self.last_ptr[0]) / dt
                self.held_v = self.held_v * 0.7 + v * 0.3
            self.last_ptr = (x, y, self.now)
            self.held_ptr = (x, y)
            return
        if self.press and self.press['on'] == 'her' and not self.press.get('moved'):
            if math.hypot(x - self.press['x'], y - self.press['y']) > DRAG_START * self.S:
                self.pick_up()
                return
        if self.mode == 'docked' and not self.out and not self.sleeping:
            if inside(self.hits.get('moth'), x, y):
                self.moth.startle_until = self.now + 0.35
                self.pop(True)
        if self.sleeping and inside(self.hits.get('her'), x, y):
            self.yoff.to(0, 12.0, 0.8)
        self.hover_cards(x, y)
        self.timer_hover(x, y)
        self.extras_motion(x, y)

    def leave(self):
        self.pointer = None
        self.hover_cards(-1, -1)
        self.timer_hover(-1e9, -1e9)

    def press_at(self, button, x, y, n=1):
        self.pointer = (x, y)
        tm = self.timer
        if tm and tm.get('edit') and not inside(self.hits.get('timer'), x, y):
            self.timer_apply()
            self.kb_hover = False
        on = self.what_at(x, y)
        if n >= 2 and on == 'her' and button == 1:
            self.click_at = None
            self.press = None
            if self.sleeping:
                return
            self.go_home()
            return
        self.press = {'button': button, 'x': x, 'y': y, 't': self.now, 'on': on}

    def release_at(self, button, x, y):
        p, self.press = self.press, None
        if self.mode == 'held':
            self.drop_her(x, y)
            return
        if not p:
            return
        on = p['on']
        if on == 'her':
            if button == 3:
                self.toggle_radial()
            elif button == 2:
                self.toggle_pin()
            elif button == 1:
                self.click_at = self.now + DOUBLE
                self.click_on = 'her'
            return
        if on == 'moth':
            self.pop(True)
            return
        if on and on.startswith('btn:'):
            idx = int(on[4:])
            if idx < len(self.buttons):
                _r, action, arg = self.buttons[idx]
                self.do(action, arg)
            return
        if on and on.startswith('card:'):
            i = int(on[5:])
            if i < len(self.cards) and self.cards[i]['path']:
                path = self.cards[i]['path']
                if button == 3:
                    self.open_card_menu(path)
                else:
                    self.requests.append(('open', path))
                    self.say('open', file=Path(path).name)

    def what_at(self, x, y):
        for i, (r, _a, _g) in enumerate(self.buttons):
            if inside(r, x, y):
                return f'btn:{i}'
        for i, c in enumerate(self.cards):
            if inside(c['rect'], x, y):
                return f'card:{i}'
        if inside(self.hits.get('her'), x, y):
            return 'her'
        if inside(self.hits.get('moth'), x, y):
            return 'moth'
        return None

    def pick_up(self):
        if self.sleeping:
            return
        self.close_all()
        self.click_at = None
        self.mode = 'held'
        self.out = True
        self.yoff.snap(0)
        self.held_ptr = self.pointer or (self.x.x, self.y.x)
        self.held_v = 0.0
        self.swing.snap(0)
        self.last_ptr = None
        if self.press:
            self.press['moved'] = True
        self.say('pickup')

    def drop_her(self, x, y):
        S = self.S
        # where her feet would be standing with the scruff at the pointer
        cx, cy = self.meta.crown
        foot_y = y - cy * S + 3 * S + self.meta.bh * S
        fx = self.clamp_x(x)
        if self.H - foot_y < DOCK_SNAP * S:
            self.mode = 'docked'
            self.dock_x = fx
            self.state['mode'] = 'docked'
            self.state['dock_x'] = fx
            self.x.snap(fx)
            self.y.snap(min(foot_y, self.H))
            self.y.to(self.H, 14.0, 0.7)
            self.yoff.snap(0)
            self.out = True
            self.say('dock')
        else:
            pose_h = self.meta.size['shy:0'][1] * S
            fy = min(max(foot_y, pose_h + 4 * S), self.H - DOCK_SNAP * S - 1)
            self.mode = 'air'
            self.state['mode'] = 'air'
            self.state['air'] = [fx, fy]
            self.x.snap(fx)
            self.y.snap(fy)
            self.say('air')
        self.requests.append(('save',))

    # ------------------------------------------------------------------ pile of files
    def pile_items(self):
        if self.pile == 'shelf':
            items = [p for p in self.state.get('shelf', []) if self.exists(p)]
        else:
            items = [str(p) for p in self.recent(MAX_CARDS)]
        return items[:MAX_CARDS]

    def toggle_pile(self, which='recent'):
        if self.pile == which:
            self.close_all()
            return
        self.menu = None
        if self.mode == 'docked' and not self.out:
            self.pop(True)
        if not self.pile:
            # the box lands under her; the spilled files go to the roomier side
            S, P = self.S, self.P
            bw = self.meta.size['prop:box'][0] * S
            side = 'left' if self.x.x > self.W / 2 else 'right'
            room = CARD_W * P + 12 * P
            lo = bw / 2 + (room if side == 'left' else 0) + 8 * S
            hi = self.W - bw / 2 - (room if side == 'right' else 0) - 8 * S
            self.pile_cx = min(max(self.x.x, lo), max(lo, hi))
            self.pile_side = side
        self.pile = which
        self.pull.clear()
        self.info = {}
        self.pile_t.to(1, 12.0, 0.75)
        self.away_since = None
        if not self.pile_items() and which == 'recent':
            self.say('pile_empty')

    pile_side = 'left'

    def box_geom(self):
        """(image x, image y, w, h) of the box; it rises from below the edge"""
        S = self.S
        bw, bh = self.meta.size['prop:box']
        t = min(max(self.pile_t.x, 0.0), 1.2)
        return self.pile_cx - bw * S / 2, self.H - bh * S * t, bw * S, bh * S

    def seat(self):
        """where her foot goes when she sits on the box (box fully up)"""
        S = self.S
        bw, bh = self.meta.size['prop:box']
        top = self.H - bh * S + BOX_TOP * S
        h = self.meta.size['sit:0'][1]
        return self.pile_cx, top + (h - SIT_SEAT) * S

    def hover_cards(self, x, y):
        for i, c in enumerate(self.cards):
            self.pull[c['path'] or i] = 1.0 if inside(grow(c['rect'], 2), x, y) else 0.0

    def open_card_menu(self, path):
        self.menu_for_files([path], anchor='card')

    # ------------------------------------------------------------------ menus
    def toggle_menu(self):
        self.toggle_radial()

    def open_menu(self, kind, data=None):
        t = lambda k, **kw: tr(self.settings, k, **kw)
        rows = []
        title = None
        if kind == 'main':
            rows = [('pile', 'recent', t('recent')), ('pile', 'shelf', t('shelf')),
                    ('pipette', None, t('pipette'))]
            if self.state.get('colors'):
                rows.append(('chips', None, ''))
            rows.append(('wake', None, t('wake')) if self.sleeping else ('sleep', None, t('sleep')))
            rows.append(('menu', 'settings', t('settings')))
            if self.mode != 'docked' or abs(self.dock_x - self.clamp_x(
                    self.W - EDGE_MARGIN * self.S - self.meta.bw * self.S / 2)) > 4:
                rows.append(('home', None, t('home')))
            rows.append(('quit', None, t('quit')))
        elif kind == 'settings':
            # the settings come in a few small groups
            rows = [('menu', 'set_her', t('cat_her')), ('menu', 'set_sound', t('cat_sound')),
                    ('menu', 'set_look', t('cat_look')), ('menu', 'set_lang', t('cat_lang')),
                    ('tour_again', None, t('tour_again'))]
            home_x = self.clamp_x(self.W - EDGE_MARGIN * self.S - self.meta.bw * self.S / 2)
            if self.mode != 'docked' or abs(self.dock_x - home_x) > 4:
                rows.append(('home', None, t('home')))
            rows.append(('quit', None, t('quit')))
            rows.append(('menu', 'main', t('back')))
        elif kind == 'set_her':
            rows = [('set', 'pinned', t('pinned')), ('set', 'reactions', t('reactions')),
                    ('set', 'chatter', t('chatter')), ('set', 'idle_nap', t('idle_nap')),
                    ('set', 'breaks', t('breaks')), ('menu', 'settings', t('back'))]
        elif kind == 'set_sound':
            s = self.settings
            rows = [('set', 'voice_vol', t('voice')), ('sound_pick', None, t('timer_sound'))]
            if s.get('timer_sound'):
                rows.append(('sound_reset', None, t('sound_reset')))
            rows += [('sound_test', None, t('sound_test')), ('menu', 'settings', t('back'))]
        elif kind == 'set_look':
            s = self.settings
            rows = [('set', 'recolor', t('follow'))]
            if not s.get('recolor', True) or 'recolor' in self.unavailable:
                rows.append(('swatches', None, ''))
            rows.append(('set', 'outfit', t('outfit')))
            if self.monitors > 1:
                rows.append(('monitor', None, t('monitor')))
            rows.append(('menu', 'settings', t('back')))
        elif kind == 'set_lang':
            rows = [('set', 'ui_lang', t('ui_lang')), ('set', 'speech_lang', t('speech_lang')),
                    ('menu', 'settings', t('back'))]
        elif kind == 'files':
            paths = data['paths']
            kinds = {J.kind_of(p) for p in paths}
            media = len(kinds) == 1 and None not in kinds
            if media:
                rows.append(('menu', 'convert', t('convert')))
                rows.append(('menu', 'compress', t('compress')))
            shelf = self.state.get('shelf', [])
            if all(p in shelf for p in paths):
                rows.append(('unshelf', None, t('unkeep')))
            else:
                rows.append(('shelf', None, t('keep')))
            if len(paths) == 1:
                rows.insert(0, ('open', None, t('open')))
            rows.append(('close', None, t('cancel')))
            title = Path(paths[0]).name if len(paths) == 1 else t('files', n=len(paths))
        elif kind == 'convert':
            paths = data['paths']
            common = None
            for p in paths:
                ts = J.targets_for(p)
                common = ts if common is None else [x for x in common if x in ts]
            for fmt in common or []:
                rows.append(('job', ('convert', fmt), fmt.upper()))
            rows.append(('menu', 'files', t('back')))
            title = t('convert_to')
        elif kind == 'compress':
            paths = data['paths']
            for key in J.presets_for(paths[0]):
                rows.append(('job', ('compress', key), t(key)))
            rows.append(('menu', 'files', t('back')))
            title = t('compress_how')
        self.menu = {'kind': kind, 'rows': rows, 'title': title,
                     'data': data or (self.menu or {}).get('data')}
        self.away_since = None

    def menu_for_files(self, paths, anchor='her'):
        if self.pile:
            self.leave_box()
        if self.mode == 'docked' and not self.out:
            self.pop(True)
        self.open_menu('files', {'paths': list(paths)})

    def do(self, action, arg):
        data = (self.menu or {}).get('data') or {}
        if self.extras_do(action, arg):
            return
        if action == 'pile':
            self.menu = None
            self.close_radial()
            self.toggle_pile(arg)
        elif action == 'menu':
            if arg == 'main':
                self.menu = None
                self.open_radial()
            else:
                self.close_radial()
                self.open_menu(arg, data)
        elif action == 'pipette':
            self.menu = None
            self.close_radial()
            self.start_pipette()
        elif action == 'timer':
            self.close_radial()
            self.timer_new()
        elif action == 'timer_toggle':
            self.timer_toggle()
        elif action == 'timer_stop':
            self.timer = None
            self.kb_hover = False
            self.requests.append(('sound_stop',))
        elif action == 'timer_edit':
            self.timer_edit()
        elif action == 'timer_reset':
            self.timer = None
            self.kb_hover = False
        elif action == 'chip':
            self.requests.append(('copy', arg))
            self.say('copied', hex=arg)
        elif action == 'sleep':
            self.menu = None
            self.close_radial()
            self.requests.append(('dnd', True))
        elif action == 'wake':
            self.menu = None
            self.close_radial()
            self.requests.append(('dnd', False))
        elif action == 'home':
            self.go_home()
        elif action == 'quit':
            self.requests.append(('quit',))
        elif action == 'set':
            s = self.settings
            if arg in self.unavailable:
                return
            if arg in self.cycles():
                opts = self.cycles()[arg]
                cur = s.get(arg)
                s[arg] = opts[(opts.index(cur) + 1) % len(opts)] if cur in opts else opts[0]
                if arg == 'outfit':
                    self.requests.append(('outfit',))
            else:
                s[arg] = not s.get(arg, arg != 'pinned')
                if arg == 'pinned' and s[arg]:
                    self.pop(True)
            self.requests.append(('save_settings',))
            if arg == 'recolor':
                self.requests.append(('recolor',))
            if arg == 'voice_vol' and s[arg]:
                self.say_sample()
            self.open_menu(self.menu['kind'] if self.menu else 'settings', data)
        elif action == 'monitor':
            self.requests.append(('monitor_next',))
        elif action == 'sound_pick':
            self.requests.append(('pick_sound',))
        elif action == 'sound_reset':
            self.settings['timer_sound'] = None
            self.requests.append(('save_settings',))
            self.requests.append(('sound', 'timer'))
            self.open_menu('set_sound', data)
        elif action == 'sound_test':
            self.requests.append(('sound', 'timer'))
        elif action == 'hue':
            self.settings['hue'] = arg
            self.requests.append(('save_settings',))
            self.requests.append(('recolor',))
        elif action == 'open':
            for p in data.get('paths', []):
                self.requests.append(('open', p))
            self.menu = None
        elif action == 'shelf':
            shelf = self.state.setdefault('shelf', [])
            for p in data.get('paths', []):
                if p not in shelf:
                    shelf.insert(0, p)
            del shelf[40:]
            self.requests.append(('save',))
            names = data.get('paths', [])
            self.say('shelf_add', file=Path(names[0]).name if names else '')
            self.menu = None
        elif action == 'unshelf':
            shelf = self.state.setdefault('shelf', [])
            for p in data.get('paths', []):
                if p in shelf:
                    shelf.remove(p)
            self.requests.append(('save',))
            names = data.get('paths', [])
            self.say('shelf_remove', file=Path(names[0]).name if names else '')
            self.menu = None
        elif action == 'job':
            op, a = arg
            for p in data.get('paths', []):
                self.requests.append(('job', op, p, a))
            self.say('job_start')
            self.menu = None
        elif action == 'card_btn':
            what, path = arg
            if what == 'star':
                shelf = self.state.setdefault('shelf', [])
                if path in shelf:
                    shelf.remove(path)
                    self.say('shelf_remove', file=Path(path).name)
                else:
                    shelf.insert(0, path)
                    self.say('shelf_add', file=Path(path).name)
                self.requests.append(('save',))
            else:
                self.menu_for_files([path])
                self.open_menu(what, {'paths': [path]})
        elif action == 'tab':
            self.pile = arg
            self.pull.clear()
        elif action == 'close':
            self.menu = None

    # ------------------------------------------------------------------ pipette, drops, jobs
    def start_pipette(self):
        self.pipette = True
        self.say('pipette_start')
        self.requests.append(('pipette',))

    def pipette_done(self, hexc):
        self.pipette = False
        if not hexc:
            return
        colors = self.state.setdefault('colors', [])
        if hexc in colors:
            colors.remove(hexc)
        colors.insert(0, hexc)
        del colors[8:]
        self.requests.append(('copy', hexc))
        self.requests.append(('save',))
        self.say('pipette_done', hex=hexc)

    def dnd_motion(self, x, y):
        """an outside drag hovers: True when she would take it"""
        self.dnd_over = True
        if self.mode == 'docked' and not self.out and not self.sleeping:
            if inside(grow(self.hits.get('moth') or (0, 0, 0, 0), 20 * self.S), x, y):
                self.pop(True)
        return inside(self.hits.get('her'), x, y) or inside(self.hits.get('moth'), x, y)

    def dnd_leave(self):
        self.dnd_over = False

    def dnd_drop(self, paths, x, y):
        self.dnd_over = False
        paths = [str(p) for p in paths if p]
        if not paths:
            return False
        if self.sleeping:
            self.requests.append(('dnd', False))
        self.menu_for_files(paths)
        if len(paths) == 1:
            self.say('drop', file=Path(paths[0]).name)
        else:
            self.say('drop_many', n=len(paths))
        return True

    def job_progress(self, fraction, label):
        self.busy = True
        self.progress = (fraction, label)

    def job_done(self, ok, kind, out=None, saved=None, fmt=None, msg=''):
        self.busy = False
        self.progress = None
        if not ok:
            if msg.startswith('missing:'):
                self.say('missing', fmt=msg.split(':', 1)[1])
            else:
                self.say('job_fail')
            return
        if kind == 'compress':
            if saved:
                self.say('compress_done', saved=saved)
            else:
                self.say('compress_none')
        else:
            self.say('convert_done', file=Path(out).name if out else '', fmt=(fmt or '').upper())

    # ------------------------------------------------------------------ time
    def update(self, now):
        dt = min(max(now - self.now, 0.0), 0.1)
        self.now = now
        moving = False
        if self.mode == 'held':
            # the drag speed decays when the pointer stops; her legs are pushed the other way
            self.held_v *= math.exp(-dt * 9)
            self.swing.to(max(-1.0, min(1.0, -self.held_v / 500.0)), 7.0, 0.1)
            moving |= self.swing.step(dt)
        if self.pile:
            sx, sy = self.seat()
            if abs(self.x.t - sx) > 0.5 or abs(self.y.t - sy) > 0.5:
                self.x.to(sx, 11.0, 0.75)
                self.y.to(sy, 11.0, 0.75)
        for sp in (self.x, self.y, self.yoff, self.pile_t, self.radial_t):
            moving |= sp.step(dt)
        self.timer_update(now)
        self.extras_update(dt, now)
        # single click waits for a possible double click
        if self.click_at and now >= self.click_at:
            self.click_at = None
            if self.sleeping:
                self.requests.append(('dnd', False))
            else:
                self.toggle_radial()
        # long press picks her up
        p = self.press
        if p and p['on'] == 'her' and p['button'] == 1 and self.mode != 'held' \
                and now - p['t'] >= LONG_PRESS and not p.get('moved'):
            self.pick_up()
        # blink
        if now >= self.next_blink:
            self.blink_until = now + 0.14
            self.next_blink = now + random.uniform(2.0, 6.0)
        if self.bubble and now > self.bubble['until']:
            self.bubble = None
        # pile / menu close when the pointer has been away a while
        if (self.pile or self.menu or self.radial) and not self.file_drag:
            ptr_in = self.pointer is not None and self.over_anything(*self.pointer)
            if ptr_in:
                self.away_since = None
            elif self.away_since is None:
                self.away_since = now
            elif now - self.away_since > CLOSE_DELAY:
                self.close_all()
        # pinned: she is simply out (at start, after sleep, after the hotkey)
        if self.settings.get('pinned') and self.mode == 'docked' and not self.out and not self.sleeping:
            self.pop(True)
        # docked: hide when nothing needs her
        if self.mode == 'docked' and self.out and not self.sleeping:
            if self.wants_out():
                self.hide_at = None
            elif self.hide_at is None:
                self.hide_at = now + HIDE_DELAY
            elif now >= self.hide_at:
                self.hide_at = None
                self.pop(False)
        if self.sleeping and self.mode == 'docked':
            over = self.pointer is not None and inside(self.hits.get('her'), *self.pointer)
            target = 0 if over else self.hidden_off()
            if abs(self.yoff.t - target) > 0.5:
                self.yoff.to(target, 10.0, 1.0)
        # small talk
        if now >= self.next_chatter_check:
            self.next_chatter_check = now + 30
            if self.visible() and not self.sleeping and not self.bubble:
                self.say('chatter')
        for k, target in self.pull.items():
            v = self.pull_v.get(k, 0.0)
            self.pull_v[k] = v + (target - v) * min(1.0, dt * 18)
        self.moth.update(dt, now)
        return moving

    def fps(self):
        if self.mode == 'held' or self.x.v or self.y.v or self.yoff.v or self.pile_t.v or self.radial_t.v \
                or self.pile and any(0.01 < v < 0.99 for v in self.pull_v.values()):
            return 60
        if self.moth.visible() and self.moth.state in ('wander', 'emerge', 'hide'):
            return 24
        return 12

    # ------------------------------------------------------------------ drawing
    def scene(self):
        """draw ops; also refreshes the hit rects used for input"""
        S = self.S
        ops = []
        self.hits = {}
        self.cards = []
        self.buttons = []
        pose, i, key, ix, iy, bx, by, w, h = self.origin()
        shown = self.visible() or self.mode != 'docked'
        if shown:
            self.hits['her'] = (ix, iy, w * S, h * S)
        if self.moth.visible() and self.moth.under:
            ops += self.moth_ops()
        if self.pile_t.x > 0.02 and self.pile_cx:
            ops += self.box_ops()
        if shown:
            ops += self.yumi_ops(pose, i, key, ix, iy, bx, by)
            ops += self.extras_ops(pose, ix, iy, bx, by)
        if self.pile and self.pile_t.x > 0.02:
            ops += self.pile_ops()
        if self.moth.visible() and not self.moth.under:
            ops += self.moth_ops()
        if self.moth.visible():
            self.hits['moth'] = grow(self.moth.rect(), 6 * S)
        rops = self.radial_ops() if self.radial_t.x > 0.02 and shown else []
        if self.timer:
            ops += self.timer_ops(shown)
        if self.menu and shown:
            ops += self.menu_ops()
        if self.tour and shown:
            ops += self.tour_ops()
        bops = self.bubble_ops() if self.bubble and shown else []
        # a line never covers the ring: it moved above it in bubble_ops; if there was no room
        # up there, the ring is drawn on top of the bubble instead
        ops += bops + rops if self.bubble_under else rops + bops
        if self.progress:
            ops += self.progress_ops()
        return ops

    def rig_part(self, name, frame, bx, by, angle=0.0):
        S = self.S
        x, y, _w, _h, piv = self.meta.part(name)
        return ('spr', name, frame, bx + x * S, by + y * S, 1.0, angle, piv[0], piv[1])

    def yumi_ops(self, pose, i, key, ix, iy, bx, by):
        S = self.S
        t = self.now
        ops = []
        cfg = self.meta.poses.get(pose, {})
        wings = 'rest' if pose == 'stand' else cfg.get('wings', 'none')
        if self.music and wings == 'rest':
            wings = 'flutter'
        wf = {'flap': SEQ[int(t * 16) % 8], 'flutter': SEQ[int(t * 8) % 8],
              'rest': SEQ[int(t * 8 / 2.5) % 8]}.get(wings)
        if wf is not None:
            for n in ('wing_left', 'wing_right'):
                ops.append(self.rig_part(n, wf, bx, by))
        ops.append(('spr', key, 0, ix, iy, 1.0, 0.0, 0, 0))
        face = self.current_face()
        if pose == 'stand':
            if face == 'blink':
                ops.append(('spr', 'eyes', 0, bx, by, 1.0, 0.0, 0, 0))
            elif face and f'face:{face}' in self.meta.names:
                ops.append(('spr', f'face:{face}', 0, bx, by, 1.0, 0.0, 0, 0))
            elif not face:
                ops += self.gaze_overlay(bx, by)
        elif pose == 'sit':
            head = None
            if face in ('surprised', 'talkA', 'talkO', 'worried'):
                head = 'oh'
            elif face in ('blink', 'sleeping', 'happy', 'wink'):
                head = 'closed'
            elif int(t / 9) % 5 == 4 and (t % 9) < 1.4:
                head = 'yawn'
            if head:
                ops.append(('spr', f'sit:head:{head}', 0, ix, iy, 1.0, 0.0, 0, 0))
        if self.pipette and pose in ('stand', 'hover'):
            # held in the sleeve on the viewer's left, tip down and a little outwards
            ops.append(('spr', 'prop:eyedropper', 0, bx - 2 * S, by + 108 * S, 1.0, 0.3, 11, 50))
        ops += self.hat_ops('hat', bx, by)
        ant = cfg.get('antennae', 'normal') if pose != 'stand' else 'normal'
        speed = 2 if self.music else 4
        af = SEQ[int(t * 8 / speed) % 8]
        for n, droop in (('antenna_left', 0.5), ('antenna_right', -0.5)):
            if ant == 'droop':
                ops.append(self.rig_part(n, 2, bx, by, droop))
            else:
                ops.append(self.rig_part(n, af, bx, by))
        ops += self.hat_ops('hat:front', bx, by)      # the lips of the slits, over her antennae
        return ops

    def moth_ops(self):
        w, h = self.moth.size()
        return [('spr', 'moth', self.moth.frame(self.now), round(self.moth.x - w / 2),
                 round(self.moth.y - h / 2), 1.0, 0.0, 0, 0)]

    def text_size(self, text, bold=False):
        return self.measure(text, bold)

    # ---- the box and the files spilled out of it
    def box_ops(self):
        x, y, w, h = self.box_geom()
        self.hits['box'] = (x, y, w, h)
        return [('spr', 'prop:box', 0, round(x), round(y), 1.0, 0.0, 0, 0)]

    def file_line(self, path):
        """second line of a card: 'PNG · 1,2 MB'"""
        info = self.info.get(path)
        if info is None:
            try:
                size = Path(path).stat().st_size
            except OSError:
                size = None
            info = self.info[path] = size
        ext = J.ext_of(path).upper() or '—'
        if info is None:
            return ext
        t = lambda k: tr(self.settings, k)
        for unit, div in (('GB', 1 << 30), ('MB', 1 << 20), ('KB', 1 << 10)):
            if info >= div:
                num = f'{info / div:.1f}'.rstrip('0').rstrip('.')
                if self.settings.get('ui_lang') == 'ru':
                    num = num.replace('.', ',')
                return f'{ext} · {num} {t(unit)}'
        return f'{ext} · {info} {t("B")}'

    def pile_ops(self):
        """big cards spilled next to the box: click opens, drag takes the file away.
        Doing something with a file = dropping it on Yumi (or right-click on the card)."""
        S, P = self.S, self.P
        ops = []
        items = self.pile_items()
        bx, by, bw, bh = self.box_geom()
        cw, ch = CARD_W * P, CARD_H * P
        left = self.pile_side == 'left'
        col_x = bx + 6 * S - cw if left else bx + bw - 6 * S
        t = self.pile_t.x
        n = len(items)
        top_y = self.H - max(1, n) * (ch + CARD_GAP * P) - 2 * P
        if not items:
            label = tr(self.settings, 'empty_recent' if self.pile == 'recent' else 'empty_shelf')
            lines = self.wrap(label, cw - 12 * P)
            r = (col_x, self.H - ch - 2 * P + (1 - t) * 3 * ch, cw, ch)
            ops.append(('panel', *r, 'card'))
            lh = self.text_size('Ag')[1]
            y0 = r[1] + (ch - P - lh * len(lines)) / 2
            for m, l in enumerate(lines):
                ops.append(('text', r[0] + 6 * P, y0 + m * lh, l, 'muted'))
            self.cards.append({'rect': r, 'path': None})
            top_y = r[1]
        ease = lambda u: 1 - (1 - u) ** 3
        for k, path in enumerate(items):
            j = n - 1 - k                        # slot from the bottom; newest card on top
            slot_x = col_x + ((k * 37) % 5 - 2) * P
            slot_y = self.H - (j + 1) * (ch + CARD_GAP * P) - 2 * P
            u = ease(min(1.0, max(0.0, t * 1.6 - j * 0.09)))
            ox, oy = bx + bw / 2 - cw / 2, self.H - ch / 2
            pull = self.pull_v.get(path, 0.0)
            x = ox + (slot_x - ox) * u + (-1 if left else 1) * pull * 6 * P
            y = oy + (slot_y - oy) * u
            kind = file_kind(path)
            r = (x, y, cw, ch)
            hot = self.pull.get(path, 0) > 0
            ops.append(('panel', *r, 'card_hot' if hot else 'card'))
            # preview square: the picture itself, or a big type icon
            sq = (x + 3 * P, y + 3 * P, 19 * P, 19 * P)
            ops.append(('panel', *sq, 'inset'))
            if kind == 'image':
                ops.append(('thumb', path, sq[0] + P, sq[1] + P, sq[2] - 2 * P, sq[3] - 2 * P))
            else:
                ops.append(('icon', kind, sq[0] + 2.5 * P, sq[1] + 2.5 * P, KIND_COLOR.get(kind, 'muted'), 2))
            tx = x + 25 * P
            room = cw - 29 * P
            name = self.ellipsize(Path(path).name, room)
            ops.append(('text', tx, y + 3 * P, name, 'fg'))
            ops.append(('text', tx, y + 12 * P, self.file_line(path), 'muted'))
            self.cards.append({'rect': r, 'path': path, 'kind': kind})
        # tabs above the column
        tabs = [('recent', tr(self.settings, 'recent')), ('shelf', tr(self.settings, 'shelf'))]
        tab_w = (cw - 2 * P) / 2
        tab_h = 13 * P
        ty = min(top_y, self.H - ch) - tab_h - 2 * P + (1 - t) * 30 * P
        for k, (name, lbl) in enumerate(tabs):
            r = (col_x + k * (tab_w + 2 * P), ty, tab_w, tab_h)
            on = self.pile == name
            ops.append(('panel', *r, 'tab_on' if on else 'tab'))
            lbl = self.ellipsize(lbl, tab_w - 8 * P)
            tw, th = self.text_size(lbl)
            ops.append(('text', r[0] + (tab_w - tw) / 2, r[1] + (tab_h - th) / 2, lbl, 'fg' if on else 'muted'))
            self.buttons.append((r, 'tab', name))
        xs = [c['rect'][0] for c in self.cards] + [col_x, bx]
        xe = [c['rect'][0] + c['rect'][2] for c in self.cards] + [col_x + cw, bx + bw]
        self.hits['pile'] = (min(xs) - 10 * P, ty - 4 * P, max(xe) - min(xs) + 20 * P, self.H - ty + 4 * P)
        return ops

    def ellipsize(self, text, width):
        if self.text_size(text)[0] <= width:
            return text
        stem, dot, ext = text.rpartition('.')
        tail = ('.' + ext) if dot and len(ext) <= 5 else ''
        base = stem if tail else text
        while base and self.text_size(base + '…' + tail)[0] > width:
            base = base[:-1]
        return base + '…' + tail

    # ---- menus
    ICON_FOR = {'pipette': 'drop', 'sleep': 'dnd', 'wake': 'dnd', 'home': 'home', 'quit': 'quit',
                'sound_pick': 'note', 'sound_test': 'play', 'sound_reset': 'back',
                'tour_again': 'star', 'monitor': 'home', 'open': 'other', 'shelf': 'star', 'unshelf': 'star_on', 'close': 'back'}

    def row_icon(self, action, arg):
        if action == 'pile':
            return 'box' if arg == 'recent' else 'shelf'
        if action == 'menu':
            return {'settings': 'gear', 'convert': 'convert', 'compress': 'compress',
                    'set_her': 'heart', 'set_sound': 'note', 'set_look': 'image',
                    'set_lang': 'doc'}.get(arg, 'back')
        if action == 'job':
            return 'next'
        return self.ICON_FOR.get(action)

    def menu_ops(self):
        S, P = self.S, self.P
        kind = self.menu['kind']
        if kind in ('files', 'convert', 'compress'):
            return self.action_menu_ops()
        ops = []
        rows = self.menu['rows']
        rh = ROW_H * P
        colors = self.state.get('colors', [])[:8]
        widths = []
        for action, arg, label in rows:
            extra = 16 * P if action == 'set' else 0
            if action == 'set' and arg in self.unavailable:
                extra = self.text_size(tr(self.settings, 'n_a'))[0] + 10 * P
            if action == 'set' and arg in self.cycles():
                extra = max(14 * P, self.text_size(self.cycle_text(
                    arg, max(self.cycles()[arg], key=lambda v: self.text_size(self.cycle_text(arg, v))[0])))[0] + 8 * P) + 2 * P
            if action == 'sound_pick':
                cur = self.settings.get('timer_sound')
                extra = (min(self.text_size(Path(cur).name)[0], 52 * P) if cur else
                         self.text_size(tr(self.settings, 'sound_default'))[0]) + 14 * P
            widths.append(self.text_size(label)[0] + 22 * P + extra)
        if any(a == 'swatches' for a, _g, _l in rows):
            widths.append(len(SWATCHES) * 11 * P + 8 * P)
        w = max(widths + [len(colors) * 10 * P + 8 * P, 60 * P])
        h = len(rows) * rh + 6 * P + (2 * P if any(a == 'swatches' for a, _g, _l in rows) else 0)
        x, y = self.menu_pos(w, h)
        ops.append(('panel', x, y, w, h, 'window'))
        self.hits['menu'] = (x, y, w, h)
        ry = y + 3 * P
        for action, arg, label in rows:
            r = (x + 2 * P, ry, w - 4 * P, rh)
            if action == 'chips':
                cxp = r[0] + 4 * P
                for c in colors:
                    cr = (cxp, ry + 2 * P, 8 * P, 8 * P)
                    ops.append(('chip', *cr, c))
                    self.buttons.append((cr, 'chip', c))
                    cxp += 10 * P
                ry += rh
                continue
            if action == 'swatches':
                cur = self.settings.get('hue')
                cxp = r[0] + 4 * P
                for hue in SWATCHES:
                    cr = (cxp, ry + 2 * P, 9 * P, 9 * P)
                    if hue == cur:
                        ops.append(('panel', cr[0] - 2 * P, cr[1] - 2 * P, cr[2] + 4 * P, cr[3] + 4 * P, 'tab_on'))
                    ops.append(('chip', *cr, swatch_hex(hue)))
                    if hue is None:
                        ops.append(('rect', cr[0] + 3 * P, cr[1] + 3 * P, 3 * P, 3 * P, 'paper', 1.0))
                    self.buttons.append((cr, 'hue', hue))
                    cxp += 11 * P
                ry += rh + 2 * P
                continue
            hot = self.pointer is not None and inside(r, *self.pointer) and not (
                action == 'set' and arg in self.unavailable)
            if hot:
                ops.append(('panel', *r, 'row_hot'))
            color = 'red' if action == 'quit' else 'muted' if (
                action in ('menu', 'close') and arg in ('main', 'files', None)) or (
                action == 'menu' and arg == 'settings' and kind != 'main') or (
                action == 'set' and arg in self.unavailable) else 'fg'
            ic = 'back' if action == 'menu' and arg == 'settings' and kind != 'main' else \
                self.row_icon(action, arg)
            if ic:
                ops.append(('icon', ic, r[0] + 3 * P, ry + (rh - 7 * P) / 2, 'accent' if hot else color))
            th = self.text_size(label)[1]
            ops.append(('text', r[0] + 13 * P, ry + (rh - th) / 2, label, color))
            if action == 'sound_pick':
                cur = self.settings.get('timer_sound')
                txt = self.ellipsize(Path(cur).name, 52 * P) if cur else tr(self.settings, 'sound_default')
                tw, th2 = self.text_size(txt)
                pr = (r[0] + r[2] - tw - 10 * P, ry + 1.5 * P, tw + 8 * P, rh - 3 * P)
                ops.append(('panel', *pr, 'button_hot' if hot else 'button'))
                ops.append(('text', pr[0] + 4 * P, pr[1] + (pr[3] - P - th2) / 2, txt, 'fg'))
            if action == 'set' and arg in self.unavailable:
                txt = tr(self.settings, 'n_a')
                tw, th2 = self.text_size(txt)
                ops.append(('text', r[0] + r[2] - tw - 4 * P, ry + (rh - th2) / 2, txt, 'muted'))
            elif action == 'set':
                val = self.settings.get(arg)
                if arg in self.cycles():
                    txt = self.cycle_text(arg, val)
                    tw, th2 = self.text_size(txt)
                    pw = max(14 * P, tw + 8 * P)
                    pr = (r[0] + r[2] - pw - 2 * P, ry + 1.5 * P, pw, rh - 3 * P)
                    ops.append(('panel', *pr, 'button_hot' if hot else 'button'))
                    tw, th2 = self.text_size(txt)
                    ops.append(('text', pr[0] + (pr[2] - tw) / 2, pr[1] + (pr[3] - P - th2) / 2, txt, 'fg'))
                else:
                    ops.append(('switch', r[0] + r[2] - 16 * P, ry + (rh - 7 * P) / 2,
                                bool(val if val is not None else arg != 'pinned')))
            self.buttons.append((r, action, arg))
            ry += rh
        return ops

    def menu_pos(self, w, h):
        S, P = self.S, self.P
        hr = self.hits.get('her') or (self.x.x - 50, self.H - 200, 100, 200)
        on_right = hr[0] + hr[2] / 2 > self.W / 2
        x = hr[0] - w + 14 * S if on_right else hr[0] + hr[2] - 14 * S
        y = hr[1] + 60 * S
        x = min(max(x, 4 * P), self.W - w - 4 * P)
        y = min(max(y, 4 * P), self.H - h - 4 * P)
        return x, y

    def action_menu_ops(self):
        """what to do with dropped files: a title and plain labelled buttons, nothing else"""
        P = self.P
        ops = []
        rows = self.menu['rows']
        title = self.menu.get('title') or ''
        bh, gap = 15 * P, 3 * P
        lh = self.text_size('Ag')[1]
        title = self.ellipsize(title, 120 * P)
        w = max([self.text_size(l)[0] for _a, _g, l in rows] + [self.text_size(title)[0], 70 * P]) + 16 * P
        # formats are short: lay them out two per row
        two = self.menu['kind'] == 'convert'
        body = [r for r in rows if r[0] == 'job'] if two else rows
        tail = [r for r in rows if r[0] != 'job'] if two else []
        nrows = (len(body) + 1) // 2 + len(tail) if two else len(rows)
        h = 6 * P + lh + 4 * P + nrows * (bh + gap) + 2 * P
        x, y = self.menu_pos(w, h)
        ops.append(('panel', x, y, w, h, 'window'))
        self.hits['menu'] = (x, y, w, h)
        ops.append(('text', x + 6 * P, y + 5 * P, title, 'muted'))
        by = y + 6 * P + lh + 3 * P
        inner = w - 12 * P

        def button(rx, ry, rw, row):
            action, arg, label = row
            r = (rx, ry, rw, bh)
            hot = self.pointer is not None and inside(r, *self.pointer)
            quiet = action in ('close',) or action == 'menu' and arg == 'files'
            ops.append(('panel', *r, 'button_hot' if hot else 'tab' if quiet else 'button'))
            tw, th = self.text_size(label)
            ops.append(('text', rx + (rw - tw) / 2, ry + (bh - P - th) / 2, label,
                        'muted' if quiet and not hot else 'fg'))
            self.buttons.append((r, action, arg))

        if two:
            half = (inner - 2 * P) / 2
            for k, row in enumerate(body):
                button(x + 6 * P + (k % 2) * (half + 2 * P), by + (k // 2) * (bh + gap), half, row)
            by += ((len(body) + 1) // 2) * (bh + gap)
        for row in (tail if two else rows):
            button(x + 6 * P, by, inner, row)
            by += bh + gap
        return ops

    RING_ACTIONS = ('pile', 'pipette', 'timer', 'sleep', 'wake', 'menu')

    # ---- speech
    def bubble_ops(self):
        S, P = self.S, self.P
        text = self.bubble['text']
        lines = self.wrap(text, 92 * P)
        sizes = [self.text_size(l) for l in lines]
        lh = max(s[1] for s in sizes)
        w = max(s[0] for s in sizes) + 12 * P
        h = lh * len(lines) + 8 * P
        hx, hy = self.head_top()
        x = hx - w / 2
        y = hy - h - 22 * S
        down = True
        side = False
        if y < 4 * P:
            # no room over her head: stand beside her face, on the side without the ring
            hr = self.hits.get('her') or (hx - 50, hy, 100, 200)
            ring_left = self.ring_left if self.radial else hr[0] + hr[2] / 2 > self.W / 2
            y = hy + 30 * S
            x = hr[0] + hr[2] + 4 * P if ring_left else hr[0] - w - 4 * P
            if x < 4 * P or x + w > self.W - 4 * P:
                x = hr[0] - w - 4 * P if ring_left else hr[0] + hr[2] + 4 * P
            side = True
        if 'menu' in self.hits:
            mx, my, mw, mh = self.hits['menu']
            if y + h > my and y < my + mh and x + w > mx and x < mx + mw:
                y = my - h - 6 * P
        x = min(max(x, 4 * P), self.W - w - 4 * P)
        y = min(max(y, 4 * P), self.H - h - 6 * P)
        self.bubble_under = False
        ring = [r for r, a, _g in self.buttons if a in self.RING_ACTIONS] if self.radial else []
        hit = lambda yy: any(x < rx + rw and x + w > rx and yy < ry + rh and yy + h + 4 * P > ry
                             for rx, ry, rw, rh in ring)
        if ring and hit(y) and not side:
            above = min(ry for _rx, ry, _rw, _rh in ring) - h - 8 * P
            if above >= 4 * P:
                y, down = above, True
            else:
                self.bubble_under = True
        elif ring and hit(y):
            self.bubble_under = True
        tx = min(max(hx, x + 5 * P), x + w - 5 * P)
        ops = [('panel', x, y, w, h, 'bubble')]
        if not side:
            ops.append(('tail', tx, y + h if down else y, 'down' if down else 'up'))
        # her words type out letter by letter (the box already has its final size)
        times = self.bubble.get('times')
        shown = len(text) if not times else sum(1 for t in times if t <= self.now - self.bubble['start'])
        for n, l in enumerate(lines):
            if shown <= 0:
                break
            part = l[:shown]
            if part:
                ops.append(('text', x + 6 * P, y + 4 * P + n * lh, part, 'ink'))
            shown -= len(l) + 1
        self.hits['bubble'] = (x, y, w, h)
        return ops

    def wrap(self, text, maxw):
        words = text.split()
        lines, cur = [], ''
        for wd in words:
            t = (cur + ' ' + wd).strip()
            if self.text_size(t)[0] <= maxw or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = wd
        if cur:
            lines.append(cur)
        return lines or ['']

    def progress_ops(self):
        S, P = self.S, self.P
        frac, label = self.progress
        text = f'{label} {int(frac * 100)}%'
        tw, th = self.text_size(text)
        w = max(tw + 12 * P, 60 * P)
        h = th + 13 * P
        if self.mode != 'docked' or self.out or self.sleeping:
            hx, hy = self.head_top()
        else:
            hx, hy = self.moth.x, self.moth.y - 10 * S
        x = min(max(hx - w / 2, 4 * P), self.W - w - 4 * P)
        y = hy - h - 30 * S
        if 'bubble' in self.hits:
            y = self.hits['bubble'][1] - h - 4 * P
        y = max(y, 4 * P)
        return [('panel', x, y, w, h, 'window'),
                ('text', x + 6 * P, y + 3 * P, text, 'fg'),
                ('bar', x + 6 * P, y + h - 7 * P, w - 12 * P, frac)]

    # ------------------------------------------------------------------ the ring menu
    def radial_items(self):
        t = lambda k: tr(self.settings, k)
        items = [('pile', 'recent', 'box', t('recent')), ('pile', 'shelf', 'shelf', t('shelf')),
                 ('pipette', None, 'drop', t('pipette')), ('timer', None, 'clock', t('timer')),
                 ('wake', None, 'dnd', t('wake')) if self.sleeping else ('sleep', None, 'dnd', t('sleep'))]
        items += self.custom_items()
        items.append(('menu', 'settings', 'gear', t('settings')))
        return items

    def open_radial(self):
        if self.pile:
            self.leave_box()
        self.menu = None
        if self.mode == 'docked' and not self.out:
            self.pop(True)
        self.radial = True
        self.radial_t.to(1, 13.0, 0.62)
        self.away_since = None

    def close_radial(self):
        if self.radial:
            self.radial = False
            self.radial_t.to(0, 20.0, 1.0)

    def toggle_radial(self):
        if self.radial or self.menu:
            self.menu = None
            self.close_radial()
        else:
            self.open_radial()

    def radial_ops(self):
        """round buttons on an arc beside her, each with its label on the outer side"""
        S, P = self.S, self.P
        ops = []
        items = self.radial_items()
        n = len(items)
        _p, _i, _k, ix, iy, bx, by, w, h = self.origin()
        cx = bx + self.meta.bw * S / 2
        cy = by + 105 * S
        left = cx > self.W / 2
        self.ring_left = left
        d = 20 * P
        lh = self.text_size('Ag')[1]
        # an arc around her: from just over her head, round her open side, down to her waist.
        # The radius grows until neighbours (circle + label) have room on the arc.
        # one even arc close round her: from over her head down her open side
        start, end = math.radians(-92.0), math.radians(-222.0 + 5 * max(0, n - 6))
        step = (end - start) / max(1, n - 1)
        R = max(175 * S, (n - 1) * 68 * S / abs(end - start))      # more buttons: a wider arc
        cy = by + 70 * S
        t = self.radial_t.x
        for k, (action, arg, icon, label) in enumerate(items):
            a = start + k * step
            dx, dy = math.cos(a) * R, math.sin(a) * R
            if not left:
                dx = -dx                              # mirror for a Yumi on the left half
            # stagger: each button flies out of her a little after the previous one
            u = min(1.0, max(0.0, t * 1.5 - k * 0.06))
            u = 1 - (1 - u) ** 3
            px = cx + dx * u - d / 2
            py = cy + dy * u - d / 2
            py = min(max(py, 4 * P), self.H - d - lh - 4 * P)
            tw, th = self.text_size(label)
            px = min(max(px, tw / 2 - d / 2 + 4 * P), self.W - d / 2 - tw / 2 - 4 * P)
            circ = (px, py, d, d)
            lx, ly = px + d / 2 - tw / 2, py + d + P
            lab = (lx, ly, tw, th)
            hot = self.pointer is not None and (inside(circ, *self.pointer) or inside(lab, *self.pointer))
            ops.append(('circle', px, py, d, hot))
            ops.append(('icon', icon, px + (d - 7 * P) / 2, py + (d - 7 * P) / 2 - P / 2,
                        'red' if action == 'quit' else 'paper' if hot else 'fg'))
            if u > 0.6:
                # plain white letters with a hard one-pixel shadow, no plate under them
                ops.append(('text', lx + P, ly + P, label, 'shadow'))
                ops.append(('text', lx, ly, label, 'acc_hi' if hot else 'red' if action == 'quit' else 'white'))
                self.buttons.append((lab, action, arg))
            self.buttons.append((circ, action, arg))
        if self.buttons:
            xs = [b[0][0] for b in self.buttons] + [ix]
            ys = [b[0][1] for b in self.buttons] + [iy]
            xe = [b[0][0] + b[0][2] for b in self.buttons] + [ix + w * S]
            ye = [b[0][1] + b[0][3] for b in self.buttons] + [iy + h * S]
            self.hits['radial'] = (min(xs) - 6 * P, min(ys) - 6 * P, max(xe) - min(xs) + 12 * P,
                                   max(ye) - min(ys) + 12 * P)
        return ops

    # ------------------------------------------------------------------ timer
    # timer = {'end': monotonic end while running, 'left': seconds while paused/pending,
    #          'pending': start-at time after scrolling, 'typed': digits being typed,
    #          'done': until when the finished timer stays on screen, 'total': last set length}
    def timer_seconds(self):
        tm = self.timer
        if not tm:
            return 0
        if tm.get('done'):
            return 0
        if tm.get('end') is not None:
            return max(0.0, tm['end'] - self.now)
        return tm.get('left', 0)

    def timer_set(self, secs, start_in=1.2):
        secs = int(max(0, min(secs, 24 * 3600 - 1)))
        if secs <= 0:
            self.timer = None
            self.kb_hover = False
            return
        tm = self.timer or {}
        tm['total'] = secs
        tm.pop('done', None)
        if tm.get('end') is not None:
            tm['end'] = self.now + secs
        else:
            tm['left'] = secs
            tm['pending'] = self.now + start_in if start_in is not None else None
            if start_in == 0:
                tm['end'] = self.now + secs
                tm['pending'] = None
        self.timer = tm

    def timer_new(self):
        self.timer_set(300, start_in=3.0)
        if self.mode == 'docked' and not self.out:
            self.pop(True)

    def timer_toggle(self):
        tm = self.timer
        if not tm or tm.get('done'):
            self.timer = None
            return
        if tm.get('end') is not None:
            tm['left'] = max(0.0, tm['end'] - self.now)
            tm['end'] = None
            tm['pending'] = None
        else:
            tm['end'] = self.now + tm.get('left', 0)
            tm['pending'] = None

    @staticmethod
    def step_for(v, up):
        v = v if up else v - 1
        return 60 if v < 600 else 300 if v < 3600 else 900

    def scroll(self, dy):
        """mouse wheel: over her (or the moth, or the timer) it sets the timer. dy<0 = up"""
        if self.pointer is None or dy == 0:
            return False
        x, y = self.pointer
        if not any(inside(self.hits.get(k), x, y) for k in ('her', 'moth', 'timer')):
            return False
        if self.sleeping:
            return False
        v = int(round(self.timer_seconds()))
        up = dy < 0
        for _ in range(int(abs(dy)) or 1):
            step = self.step_for(v, up)
            v = (v // step + 1) * step if up else max(0, -(-v // step) * step - step)
        if self.timer and self.timer.get('typed') is not None:
            self.timer['typed'] = None
            self.timer_edit_end()
        self.timer_set(v)
        if self.mode == 'docked' and not self.out:
            self.pop(True)
        return True

    def key(self, name):
        """keys while the timer has the keyboard (pointer over it, or after a click on the digits):
        digits overwrite the digit under the cursor, arrows move it, Enter applies, Esc cancels"""
        if not self.kb_hover:
            return False
        tm = self.timer
        if tm is None:
            self.timer_set(1, start_in=None)
            tm = self.timer
            tm['left'] = 0
        name = name.replace('KP_', '')
        editing = tm.get('typed') is not None
        if len(name) == 1 and name.isdigit():
            if not editing:
                self.timer_edit(0)
            d = list(tm['typed'])
            if tm['cur'] < 4:
                d[tm['cur']] = name
                tm['cur'] += 1
            tm['typed'] = ''.join(d)
            return True
        if name in ('BackSpace', 'Left', 'Right', 'Home', 'End'):
            if not editing:
                return True
            if name == 'BackSpace' and tm['cur'] > 0:
                tm['cur'] -= 1
                d = list(tm['typed'])
                d[tm['cur']] = '0'
                tm['typed'] = ''.join(d)
            elif name == 'Left':
                tm['cur'] = max(0, tm['cur'] - 1)
            elif name == 'Right':
                tm['cur'] = min(4, tm['cur'] + 1)
            elif name == 'Home':
                tm['cur'] = 0
            elif name == 'End':
                tm['cur'] = 4
            return True
        if name in ('Return', 'Enter'):
            self.timer_apply()
            return True
        if name == 'Escape':
            tm['typed'] = None
            self.timer_edit_end()
            if not tm.get('left') and tm.get('end') is None:
                self.timer = None
                self.kb_hover = False
            return True
        if name == 'Delete':
            self.timer = None
            self.kb_hover = False
            return True
        return False

    def digit_slot_at(self, x):
        """which of the four digits is nearest to x (set while the timer window is drawn)"""
        xs = getattr(self, 'digit_x', None)
        if not xs:
            return 0
        return min(range(4), key=lambda k: abs(xs[k] - x))

    def timer_edit(self, cur=None):
        """click on the digits (or just typing): the keyboard is hers until Enter / Esc / a click
        elsewhere. The digits start as the time on the clock, so it can be fixed in place."""
        tm = self.timer
        if not tm or tm.get('done'):
            return
        if tm.get('typed') is None:
            v = int(math.ceil(self.timer_seconds()))
            m, sec = divmod(v, 60)
            if m > 99:
                m, sec = 99, 59
            if tm.get('end') is not None:
                tm['left'] = max(0.0, tm['end'] - self.now)
                tm['end'] = None
                tm['resume'] = True
            tm['pending'] = None
            tm['typed'] = f'{m:02d}{sec:02d}'
            tm['seed'] = tm['typed']
            tm['edit'] = True
            tm['cur'] = self.digit_slot_at(self.pointer[0]) if cur is None and self.pointer else (cur or 0)
        elif cur is None and self.pointer:
            tm['cur'] = self.digit_slot_at(self.pointer[0])
        self.kb_hover = True

    def timer_edit_end(self):
        tm = self.timer
        if not tm:
            return
        tm['edit'] = False
        tm.pop('seed', None)
        if tm.pop('resume', False) and tm.get('end') is None and tm.get('left'):
            tm['end'] = self.now + tm['left']

    def timer_apply(self):
        tm = self.timer
        if not tm:
            return
        typed = tm.get('typed')
        if not typed or typed == tm.get('seed'):
            tm['typed'] = None
            self.timer_edit_end()
            return
        tm['edit'] = False
        tm.pop('resume', None)
        tm.pop('seed', None)
        secs = int(typed[:2]) * 60 + int(typed[2:])
        tm['typed'] = None
        tm['end'] = None
        self.timer_set(secs, start_in=0)

    def timer_update(self, now):
        tm = self.timer
        if not tm:
            return
        if tm.get('done'):
            if now > tm['done']:
                self.timer = None
            return
        if tm.get('pending') and now >= tm['pending'] and not tm.get('typed'):
            tm['end'] = now + tm.get('left', 0)
            tm['pending'] = None
        if tm.get('end') is not None and now >= tm['end']:
            total = tm.get('total', 0)
            ru = self.settings.get('speech_lang') == 'ru'
            if total < 60:
                mins = f"{total} сек" if ru else f"{total} s"
            else:
                mins = f"{total // 60} мин" if ru else f"{total // 60} min"
            tm['end'] = None
            tm['left'] = 0
            tm['done'] = now + 12
            self.requests.append(('sound', 'timer'))
            if self.mode == 'docked' and not self.out and not self.sleeping:
                self.pop(True)
            self.say('timer_done', mins=mins)
            self.set_face('surprised', 3)

    def timer_text(self):
        tm = self.timer
        if tm and tm.get('typed') is not None:
            d = tm['typed']
            return f'{d[:2]}:{d[2:]}'
        v = int(math.ceil(self.timer_seconds()))
        h, rem = divmod(v, 3600)
        m, sec = divmod(rem, 60)
        return f'{h}:{m:02d}:{sec:02d}' if h else f'{m:02d}:{sec:02d}'

    def timer_ops(self, shown):
        S, P = self.S, self.P
        tm = self.timer
        ops = []
        text = self.timer_text()
        blink = tm.get('done') and int(self.now * 3) % 2 == 0
        if not shown or (self.mode == 'docked' and not self.out and not self.sleeping):
            # she is hidden: a small pill above the moth
            if not self.moth.visible():
                return ops
            tw, th = self.text_size(text)
            w, h = tw + 10 * P, 13 * P
            x = min(max(self.moth.x - w / 2, 4 * P), self.W - w - 4 * P)
            y = self.moth.y - self.moth.size()[1] / 2 - h - 3 * P
            ops.append(('panel', x, y, w, h, 'bubble' if blink else 'window'))
            ops.append(('text', x + 5 * P, y + (h - P - th) / 2, text, 'ink' if blink else 'fg'))
            self.hits['timer'] = (x, y, w, h)
            return ops
        title = tr(self.settings, 'timer')
        big_w = self.text_size(text)[0] * 2
        w = max(big_w + 14 * P, 92 * P)
        lh = self.text_size('Ag')[1]
        bh = 13 * P
        h = 4 * P + lh + 2 * lh + 3 * P + bh + 5 * P
        hx, hy = self.head_top()
        left = hx > self.W / 2
        x = hx - w - 78 * S if left else hx + 78 * S
        y = hy - 10 * S
        if self.radial and 'radial' in self.hits:
            rx, ry, rw, rh = self.hits['radial']
            y = min(y, ry - h - 4 * P)
        x = min(max(x, 4 * P), self.W - w - 4 * P)
        y = min(max(y, 4 * P), self.H - h - 4 * P)
        if self.tour:                                   # never under the tour panel: above it, or below
            tx, ty, tw, th, _l = self.tour_geom()
            if x < tx + tw and tx < x + w and y < ty + th and ty < y + h:
                y = ty - h - 4 * P
                if y < 4 * P:
                    y = ty + th + 4 * P
                y = min(max(y, 4 * P), self.H - h - 4 * P)
        editing = tm.get('typed') is not None
        ops.append(('panel', x, y, w, h, 'bubble' if blink else 'window'))
        ops.append(('text', x + 6 * P, y + 4 * P, title, 'ink' if blink else 'muted'))
        sp = 2 * P if editing else 0            # while editing the digits are spaced out so the
        ew = big_w + 4 * sp                      # cursor has a gap of its own to sit in
        dx0 = x + (w - ew) / 2
        box = (dx0 - 4 * P, y + 4 * P + lh, ew + 8 * P, 2 * lh)
        digits_hot = self.pointer is not None and inside(box, *self.pointer) and not tm.get('done')
        if editing:
            ops.append(('panel', *box, 'inset'))
        elif digits_hot:
            ops.append(('panel', *box, 'row_hot'))
        if not tm.get('done'):
            self.buttons.append((box, 'timer_edit', None))
            self.digit_x = [dx0 + self.text_size(text[:i])[0] * 2 + i * sp + self.text_size(text[i])[0]
                            for i in (0, 1, 3, 4)]
        ty = y + 4 * P + lh
        if editing:
            for i, ch in enumerate(text):
                ops.append(('text', dx0 + self.text_size(text[:i])[0] * 2 + i * sp, ty, ch, 'accent', 2))
            if int(self.now * 2) % 2 == 0:
                cur = tm.get('cur', 0)
                # a vertical bar in the gap right after the digit before the cursor
                idx = (0, 1, 3, 4)
                if cur == 0:
                    cx0 = dx0 - 2 * P
                else:
                    k = idx[cur - 1]
                    cx0 = dx0 + self.text_size(text[:k + 1])[0] * 2 + k * sp + P / 2
                ops.append(('rect', cx0, ty + 4 * P, P, 2 * lh - 8 * P, 'fg', 1.0))
        else:
            tcol = 'accent' if digits_hot else ('ink' if blink else 'fg')
            ops.append(('text', dx0, ty, text, tcol, 2))
        by = y + h - bh - 4 * P
        bw = (w - 14 * P) / 2
        running = tm.get('end') is not None
        if tm.get('done'):
            # finished: one wide Stop button, it silences the chime and closes the window
            labels = [(tr(self.settings, 'stop'), 'timer_stop')]
            bw = w - 12 * P
        else:
            labels = [(tr(self.settings, 'pause') if running else tr(self.settings, 'resume'), 'timer_toggle'),
                      (tr(self.settings, 'reset'), 'timer_reset')]
        for k, (lbl, act) in enumerate(labels):
            r = (x + 6 * P + k * (bw + 2 * P), by, bw, bh)
            hot = self.pointer is not None and inside(r, *self.pointer)
            ops.append(('panel', *r, 'button_hot' if hot else 'button'))
            tw, th = self.text_size(lbl)
            ops.append(('text', r[0] + (bw - tw) / 2, r[1] + (bh - P - th) / 2, lbl, 'fg'))
            self.buttons.append((r, act, None))
        self.hits['timer'] = (x, y, w, h)
        return ops

    def timer_hover(self, x, y):
        tm = self.timer
        over = tm is not None and inside(self.hits.get('timer'), x, y)
        editing = bool(tm and tm.get('edit'))
        if self.kb_hover and not over and not editing and tm and tm.get('typed'):
            self.timer_apply()
        self.kb_hover = (over or editing) and tm is not None and not tm.get('done')

    def input_rects(self):
        return [tuple(int(round(v)) for v in r) for r in self.hits.values()] + \
               [tuple(int(round(v)) for v in c['rect']) for c in self.cards] + \
               [tuple(int(round(v)) for v in b[0]) for b in self.buttons]

    def card_path_at(self, x, y):
        for c in self.cards:
            if c['path'] and inside(c['rect'], x, y):
                return c['path']
        return None

#!/usr/bin/env python3
"""Her round buttons: order, switches, your own ones, the editor on the ring.
Run: python3 tests/test_ring_editor.py"""
import string
import sys
import tempfile
from pathlib import Path

import gi
gi.require_version('GdkPixbuf', '2.0')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from yumi import config, pixfont, pixui  # noqa: E402
from yumi.brain import Brain  # noqa: E402
from yumi.phrases import Speech  # noqa: E402
from yumi.sprites import Meta  # noqa: E402


def brain():
    settings = {}
    b = Brain(Meta(config.SPRITES, None), settings, {'intro_done': True}, Speech(settings), lambda *_a: (10, 10))
    b.custom = []
    return b


def ids(b):
    return [it[4] for it in b.radial_items()]


def rect_of(b, action, arg=None):
    b.scene()
    return next(r for r, a, g in b.buttons if a == action and (arg is None or g == arg))


def center(r):
    return r[0] + r[2] / 2, r[1] + r[3] / 2


def settle(b):
    for k in range(120):
        b.update(k / 30)


assert all(len(row) == 7 for g in pixui.ICONS.values() for row in g) and all(len(g) == 7 for g in pixui.ICONS.values())

b = brain()
DEFAULT = ['recent', 'shelf', 'pipette', 'timer', 'sleep', 'settings']
assert ids(b) == DEFAULT
b.settings['ring'] = ['settings', 'gone-button', 'shelf']     # a stale id is dropped, new ones come after
assert ids(b) == ['settings', 'shelf', 'recent', 'pipette', 'timer', 'sleep']
b.settings['ring'] = []

# the switches; a sleeping Yumi keeps the way to wake her; settings and your own buttons do not switch off
b.do('ring_on', 'timer')
assert 'timer' not in ids(b) and b.settings['ring_off'] == ['timer']
b.do('ring_on', 'timer')
assert ids(b) == DEFAULT
b.settings['ring_off'] = ['settings', 'cmd:foot', 'sleep']
b.custom = [{'label': 'f', 'label_ru': 'f', 'icon': 'star', 'command': 'foot'}]
assert 'settings' in ids(b) and 'cmd:foot' in ids(b) and 'sleep' not in ids(b)
b.set_sleep(True)
assert 'sleep' in ids(b) and b.radial_items()[4][0] == 'wake'
b = brain()

# settings opens the editor at once; a switched-off button stays in it, nothing closes it but Done
b.open_menu('settings')
assert ('ring_edit', None, config.tr(b.settings, 'cat_ring')) in b.menu['rows']
b.do('ring_on', 'timer')
b.do('ring_edit', None)
assert b.menu is None
settle(b)
b.scene()
assert b.radial and b.ring_edit and len(b.ring_slots) == config.RING_SLOTS
assert sum(a == 'ring_tile' for _r, a, _g in b.buttons) == len(DEFAULT)
assert sum(a == 'ring_add' for _r, a, _g in b.buttons) == config.RING_SLOTS - len(DEFAULT)
assert {g for _r, a, g in b.buttons if a == 'ring_on'} == set(DEFAULT) - {'settings'}
assert not any(a == 'ring_del' for _r, a, _g in b.buttons)     # built-in buttons cannot be deleted
b.leave()
for k in range(5):
    b.update(100.0 + k * 2)
assert b.radial and b.ring_edit

# the switch on a tile is hit before the tile
x, y = center(rect_of(b, 'ring_on', 'timer'))
b.press_at(1, x, y)
assert b.ring_drag is None
b.release_at(1, x, y)
assert 'timer' in ids(b) and b.ring_edit

# drag: pressed, dropped on another place, saved once; a free place stops it after the last button
r = rect_of(b, 'ring_tile', 'recent')
b.press_at(1, *center(r))
assert b.ring_drag == 'recent'
b.motion(*b.ring_slots[3])
assert ids(b) == ['shelf', 'pipette', 'timer', 'recent', 'sleep', 'settings']
b.scene()
b.motion(*b.ring_slots[8])
assert ids(b)[-1] == 'recent'
b.requests.clear()
b.release_at(1, *b.ring_slots[8])
assert b.ring_drag is None and ('save_settings',) in b.requests and b.ring_edit

# a click on a built-in button does nothing, on yours it edits it, a jitter is still a click
b.settings['ring'] = []
b.requests.clear()
x, y = center(rect_of(b, 'ring_tile', 'shelf'))
b.press_at(1, x, y)
b.release_at(1, x, y)
assert not b.requests
b.ring_add('foot', 'Term', 'terminal')
assert 'cmd:foot' in ids(b) and ('save_actions',) in b.requests
b.requests.clear()
x, y = center(rect_of(b, 'ring_tile', 'cmd:foot'))
b.press_at(1, x, y)
b.motion(x + 2, y + 1)
b.release_at(1, x + 2, y + 1)
assert b.form['old'] == 'foot' and b.form['cmd'] == 'foot' and b.form['name'] == 'Term' and b.form['icon'] == 'terminal'

# the form: modal, typing at the caret, Tab, keys; empty fields do not save, Esc leaves it all as it was
b.scene()
assert all(a.startswith('form_') for _r, a, _g in b.buttons)
x, y = center(b.hits['her'])
b.press_at(1, x, y)
b.release_at(1, x, y)
assert b.click_at is None and b.mode != 'held'                 # she is not clicked through the form
b.key('Home')
b.key('z', 'z')
assert b.form['cmd'] == 'zfoot' and b.form['pos'] == 1
b.key('Delete')
assert b.form['cmd'] == 'zoot'
b.key('End')
b.key('BackSpace')
b.key('Left')
assert b.form['cmd'] == 'zoo' and b.form['pos'] == 2
b.key('Escape')
assert b.form is None and b.custom[0]['command'] == 'foot' and b.ring_edit
b.do('ring_add', None)
assert b.form == {'old': None, 'cmd': '', 'pos': 0, 'field': 'cmd', 'name': '', 'icon': 'star'}
b.key('Return')
b.scene()
assert b.form and not any(a == 'form_save' for _r, a, _g in b.buttons)
for ch in 'mpv x':
    b.key(ch, ch)
b.key('Tab')
assert b.form['field'] == 'name'
b.form_text('Плеер\nочень длинное название')              # paste: one line, the name has a limit
assert len(b.form['name']) == 18 and '\n' not in b.form['name']
x, y = center(rect_of(b, 'form_icon', 'cup'))
b.press_at(1, x, y)
b.release_at(1, x, y)
x, y = center(rect_of(b, 'form_field', 'cmd'))
b.press_at(1, x, y)
b.release_at(1, x, y)
assert b.form['field'] == 'cmd' and b.form['pos'] == 5
b.key('Return')
assert b.form is None and b.custom[-1]['command'] == 'mpv x' and b.custom[-1]['icon'] == 'cup'
b.do('ring_add', None)
b.close_radial()                                               # the hotkey, sleep...: the form goes too
assert b.form is None
b.do('ring_edit', None)
settle(b)
b.leave()
b.custom = [c for c in b.custom if c['command'] == 'foot']

# editing keeps the button's place, its other language's name, and renames it in the saved order
b.custom = [{'label': 'A', 'label_ru': 'А', 'icon': 'star', 'command': 'a'},
            {'label': 'B', 'label_ru': 'Б', 'icon': 'star', 'command': 'b'}]
b.settings['ring'] = ['cmd:b', 'settings', 'cmd:a']
b.settings['ui_lang'] = 'ru'
b.ring_add('a2', 'Я', 'gear', old='a')
assert [a['command'] for a in b.custom] == ['a2', 'b']
assert b.custom[0] == {'label': 'A', 'label_ru': 'Я', 'icon': 'gear', 'command': 'a2'}
assert ids(b)[:3] == ['cmd:b', 'settings', 'cmd:a2']
b.ring_add('b', 'Х', 'gear', old='a2')                         # to another button's command: one remains
assert [a['command'] for a in b.custom] == ['b'] and ids(b).count('cmd:b') == 1
b.ring_add('new', 'N', 'star')
assert [a['label_ru'] for a in b.custom] == ['Х', 'N']
b.ring_add('new', 'N2', 'star')
assert [a['label_ru'] for a in b.custom] == ['Х', 'N2']        # same command: replaced
b.ring_add('new2', 'N3', 'star', old='new')
assert b.custom[-1]['label'] == b.custom[-1]['label_ru'] == 'N3'   # one name for both languages stays one
b.settings['ui_lang'] = 'en'

# the minus removes your own button for good
b.settings['ring'] = []
b.custom = [{'label': 'f', 'label_ru': 'f', 'icon': 'star', 'command': 'foot'}]
x, y = center(rect_of(b, 'ring_del', 'cmd:foot'))
b.press_at(1, x, y)
b.release_at(1, x, y)
assert not b.custom and 'cmd:foot' not in ids(b) and b.ring_edit

# a full ring has no plus
b.custom = [{'label': str(i), 'label_ru': str(i), 'icon': 'star', 'command': str(i)} for i in range(4)]
b.scene()
assert len(b.ring_slots) == config.RING_SLOTS and not any(a == 'ring_add' for _r, a, _g in b.buttons)

# Done goes back to the settings; an own picture draws as a thumb, a lost one as a star
b.do('ring_done', None)
assert not b.ring_edit and not b.radial and b.menu['kind'] == 'settings'
b = brain()
b.exists = lambda p: p == '/pic.png'
b.custom = [{'label': 'a', 'label_ru': 'a', 'icon': '/pic.png', 'command': 'a'},
            {'label': 'b', 'label_ru': 'b', 'icon': '/lost.png', 'command': 'b'}]
assert [it[2] for it in b.custom_items()] == ['/pic.png', 'star']
b.open_radial()
for k in range(20):           # not long enough to close on its own
    b.update(k / 30)
assert any(o[0] == 'thumb' and o[1] == '/pic.png' for o in b.scene())

# everything you can type in a command has a real glyph
assert all(c in pixfont.GLYPHS for c in string.ascii_letters + string.digits + string.punctuation)

# the file: round trip, a broken one is kept aside rather than overwritten
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / 'actions.json'
    config.save_actions([{'label': 'Ю', 'label_ru': 'Ю', 'icon': 'star', 'command': 'x'}], p)
    assert config.load_actions(p)[0]['label'] == 'Ю'
    p.write_text('[{oops')
    assert config.load_actions(p) == [] and (Path(d) / 'actions.json.bad').read_text() == '[{oops'
    assert config.load_actions(p) == []
    pic = Path(d) / 'a.png'
    pic.write_bytes(b'x')
    orig, config.CONFIG_DIR = config.CONFIG_DIR, Path(d)
    kept = config.keep_icon(pic)
    config.CONFIG_DIR = orig
    assert kept != str(pic) and Path(kept).read_bytes() == b'x'

print('ok')

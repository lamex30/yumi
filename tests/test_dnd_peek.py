#!/usr/bin/env python3
"""In Do Not Disturb she can be used without waking up; an idle nap still ends on a click.
Run: python3 tests/test_dnd_peek.py"""
import sys
from pathlib import Path

import gi
gi.require_version('GdkPixbuf', '2.0')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from yumi import config  # noqa: E402
from yumi.brain import Brain  # noqa: E402
from yumi.phrases import Speech  # noqa: E402
from yumi.sprites import Meta  # noqa: E402


def asleep(nap=False):
    settings = {}
    b = Brain(Meta(config.SPRITES, None), settings, {'intro_done': True}, Speech(settings), lambda *_a: (10, 10))
    b.set_sleep(True, nap=nap)
    b.nap = nap
    return b


def wakes(b):
    return ('dnd', False) in b.requests


b = asleep()
assert b.pose() == ('sleep', 0) and not b.peek
b.toggle()                        # the hotkey opens her menu, eyes half-open, DND stays
assert b.radial and b.peeks() and not wakes(b) and b.sleeping
b.peek = b.peeks()
assert b.pose() == ('sleep', 1)
b.toggle()
assert not b.radial and not b.peeks()
b.dnd_drop(['/tmp/x.png'], 0, 0)  # a dropped file does not wake her either
assert not wakes(b) and b.menu
assert ('wake', None, 'dnd') == b.radial_items()[4][:3]

b = asleep()                      # the file box does not get her up either
x = b.x.t
b.toggle_pile('recent')
assert b.pile and not wakes(b) and b.pile_cx != x and b.x.t == x and b.pose()[0] == 'sleep'

b = asleep(nap=True)              # dozing because you were away: any touch ends it
b.toggle()
assert wakes(b) and not b.radial
print('ok')

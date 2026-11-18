#!/usr/bin/env python3
"""«юми» услышана: мотылек становится девочкой. Run: python3 tests/test_wake_form.py

- спрятанная у края (мотылек) выходит сразу, ещё до фразы;
- спящая (DND) и дремляющая (nap) просыпается от самого имени;
- фраза после имени идёт в мозг вместе с alt (чтение стоп-ухи);
- задумалась или слушает — имя потрачено зря, она не вылезает.
"""
import sys
from pathlib import Path

import gi
gi.require_version('GdkPixbuf', '2.0')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from yumi import config  # noqa: E402
from yumi.brain import Brain  # noqa: E402
from yumi.phrases import Speech  # noqa: E402
from yumi.sprites import Meta  # noqa: E402


def brain():
    settings = {}
    return Brain(Meta(config.SPRITES, None), settings, {'intro_done': True},
                 Speech(settings), lambda *_a: (10, 10))


# спрятанная у края: свежий мозг — это мотылек; имя выводит её сразу
b = brain()
assert not b.out and not b.sleeping, 'a fresh brain starts as the moth by the edge'
b.wake_form()
assert b.out, 'the name must bring her out at once'

# DND: спит — имя будит и поднимает из-за края
b = brain()
b.set_sleep(True)
assert b.sleeping and not b.out and b.yoff.t != 0
b.wake_form()
assert not b.sleeping and b.out and b.yoff.t == 0, 'the name must wake her'

# дремля: имя заканчивает nap, а не «протухшим» DND-выключателем
b = brain()
b.set_sleep(True, nap=True)
b.nap = True
b.wake_form()
assert not b.sleeping and not b.nap and b.out, 'the name must end the nap too'

# фраза после имени: она уже девочка, и alt доходит до heard_text
b = brain()
b.set_sleep(True)
seen = []
b.heard_text = lambda text, wake=False, alt=None: seen.append((text, wake, alt))
b.wake_up('погода', alt='юми погода')
assert not b.sleeping, 'the phrase must not be lost: she wakes first'
assert seen == [('погода', True, 'юми погода')], seen

# задумалась: имя потрачено — ни наружу, ни в мозг
b = brain()
b.thinking = True
b.wake_up('погода')
assert not b.out and not b.sleeping, 'busy: the word is spent'

# имя одной: она просыпается, спрашивает и открывает окно слушания
b = brain()
b.set_sleep(True)
b.wake_up('')
assert not b.sleeping and b.out, 'the name alone still wakes her'
assert b.listening and ('listen', 15) in b.requests, 'and she asks what you wanted'
print('ok')

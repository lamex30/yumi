#!/usr/bin/env python3
"""Her own voice commands end to end: entities, confidence, confirmation.
Run: python3 tests/test_voice_intent.py"""
import sys
import tempfile
from pathlib import Path

import gi
gi.require_version('GdkPixbuf', '2.0')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from yumi import config  # noqa: E402
from yumi.brain import Brain  # noqa: E402
from yumi.phrases import Speech  # noqa: E402
from yumi.sprites import Meta  # noqa: E402


def action(**kw):
    """one button as actions.json keeps it"""
    out = {'label': 'Button', 'label_ru': 'Кнопка', 'icon': 'star',
           'command': 'noop', 'phrases': [], 'keywords': [],
           'speak': '', 'url': '', 'id': '', 'priority': 0,
           'requires_confirmation': False, 'entities': {}}
    out.update(kw)
    return out


def brain(actions, **settings):
    s = dict(config.DEFAULT_SETTINGS)
    s['ui_lang'] = 'ru'          # the confirmation prompts are then the Russian ones
    s.update(settings)
    b = Brain(Meta(config.SPRITES, None), s,
              {'intro_done': True}, Speech(s), lambda *_a: (10, 10))
    # never read the real actions.json while this test runs
    b.actions_path = Path(tempfile.gettempdir()) / 'yumi-no-actions-test.json'
    b.actions_mtime = 0.0
    b.custom = actions
    return b


# ---- a plain phrase runs its command, and she says the «speak» line
b = brain([action(command='foot', phrases=['открой терминал'])])
b.requests.clear()
assert b.voice_intent('открой терминал') is True
assert ('run', 'foot') in b.requests, b.requests
assert b.last_intent == 'custom:foot' and b.last_confidence == 1.0
assert b.voice_intent('совсем другая фраза') is False

# ---- entities are shared: the button declares «volume» and {volume} gets 50
b = brain([action(command='vol', phrases=['громкость половина'],
                  speak='Ставлю {volume}', entities={'volume': True})])
b.requests.clear()
assert b.voice_intent('громкость половина') is True
assert ('run', 'vol') in b.requests
assert b.bubble['text'] == 'Ставлю 50', b.bubble

# ---- a url fills {query}, and commands are never built from heard words
b = brain([action(command='', keywords=['найди'],
                  url='https://example.org/?q={query}')])
b.requests.clear()
assert b.voice_intent('найди котики') is True
assert ('url', 'https://example.org/?q=%D0%BA%D0%BE%D1%82%D0%B8%D0%BA%D0%B8') in b.requests, b.requests

# ---- the confidence line: a keyword (0.80) can be left to the chat
ACTIONS = [action(command='foot', phrases=['открой терминал'], keywords=['терминал'])]
b = brain(ACTIONS, nlu_confidence=1.0)
b.requests.clear()
assert b.voice_intent('терминал') is False and not b.requests
assert b.voice_intent('открой терминал') is True
assert ('run', 'foot') in b.requests
# switching voice commands off keeps them off at any threshold
b = brain(ACTIONS, nlu=False)
assert b.voice_intent('открой терминал') is False

# ---- requires_confirmation: she asks first and waits for the next word
DANGER = action(command='rm -rf', id='wipe', phrases=['удали всё'],
                requires_confirmation=True, speak='Удаляю')
b = brain([DANGER])
b.requests.clear()
assert b.voice_intent('удали всё') is True
assert b.intent_confirm and b.intent_confirm['action'] is DANGER
assert ('run', 'rm -rf') not in b.requests, 'must not run before the answer'
assert b.bubble['text'] == config.UI['ru']['confirm_ask'], b.bubble
assert ('listen', 15) in b.requests

# the spoken «да» runs exactly the pending command, with its slots
b.heard_text('да')
assert b.intent_confirm is None
assert ('run', 'rm -rf') in b.requests, b.requests
assert b.bubble['text'] == 'Удаляю'

# «нет» cancels, and nothing runs
b = brain([DANGER])
b.requests.clear()
b.voice_intent('удали всё')
b.heard_text('нет')
assert b.intent_confirm is None
assert ('run', 'rm -rf') not in b.requests
assert b.bubble['text'] == config.UI['ru']['confirm_no']

# the browser answers the very same pending command
b = brain([DANGER])
b.requests.clear()
b.voice_intent('удали всё')
assert b._voice_confirm_web(True) is True
assert ('run', 'rm -rf') in b.requests
assert b._voice_confirm_web(True) is False, 'nothing is pending twice'
b = brain([DANGER])
b.requests.clear()
b.voice_intent('удали всё')
b._voice_confirm_web(False)
assert ('run', 'rm -rf') not in b.requests

# «стоп» drops a waiting command too
b = brain([DANGER])
b.voice_intent('удали всё')
b.stop_all()
assert b.intent_confirm is None

print('tests/test_voice_intent.py: ok')

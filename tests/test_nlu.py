#!/usr/bin/env python3
"""The voice intents of actions.json, matched without a mind. Run: python3 tests/test_nlu.py"""
import importlib.util
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
from yumi import nlu  # noqa: E402

TERMINAL = {'label': 'Terminal', 'command': 'foot',
            'phrases': ['открой терминал', 'запусти консоль', 'open terminal'],
            'keywords': ['терминал', 'консоль', 'terminal'], 'speak': 'Открываю терминал'}
MUSIC = {'label': 'Tune', 'command': 'playerctl play-pause',
         'phrases': ['врубай музыку'], 'keywords': ['плеер']}
ACTIONS = [TERMINAL, MUSIC]

# the words the way the intents read them: lower case, punctuation gone, ё folded into е
assert nlu.normalize('Ёлки, Ёж!') == ['елки', 'еж']
assert nlu.normalize('') == []
assert nlu.normalize('  юми,   время...  ') == ['юми', 'время']

# the whole phrase, letter for letter (in any polite wrapping)
for said in ('открой терминал', 'Юми, открой терминал', 'ну открой терминал пожалуйста',
             'запусти консоль', 'open terminal', 'open terminal now'):
    hit = nlu.match(said, ACTIONS)
    assert hit and hit[0] is TERMINAL and hit[1] == nlu.SCORE_PHRASE, said

# the ears' diary: what Vosk really writes for the phrases, one bent letter here and there
for said in ('открой терминай', 'открой терминалку', 'запусти консольку'):
    hit = nlu.match(said, ACTIONS)
    assert hit and hit[0] is TERMINAL and hit[1] == nlu.SCORE_FUZZY, said

# keywords alone («терминал» said patiently, the second time; the polite words
# of the English phrase go between)
for said in ('терминал', 'ну терминал же', 'плеер', 'скючи консоль', 'открой консоль',
             'please open the terminal now'):
    hit = nlu.match(said, ACTIONS)
    assert hit and hit[1] == nlu.SCORE_KEYWORD, said

# keywords with the ears' slips forgiven («терминай» for «терминал»)
for said in ('терминай', 'запусти терминай'):
    hit = nlu.match(said, ACTIONS)
    assert hit and hit[0] is TERMINAL and hit[1] == nlu.SCORE_FUZZY, said

# the opposite of a wish is never a wish
for said in ('не открывай терминал', 'не надо терминал', 'not the terminal',
             'не врубай музыку'):
    assert nlu.match(said, ACTIONS) is None, said

# not her business at all: nothing earns its keep
for said in ('включи свет', 'привет', 'который час', 'какая погода в ялте',
             'расскажи анекдот', 'включи музыку'):
    assert nlu.match(said, ACTIONS) is None, said

# a whole different phrase is not one bent letter away: «открой окно» is not the terminal
for said in ('открой окно', 'запусти игру'):
    assert nlu.match(said, ACTIONS) is None, said

# the best intent wins, the first written wins a tie
BOTH = [{'command': 'a', 'phrases': ['включи плеер']},
        {'command': 'b', 'phrases': ['включи плеер']}]
assert nlu.match('включи плеер', BOTH)[0]['command'] == 'a'
BEST = [{'command': 'b', 'keywords': ['плеер']},
        {'command': 'a', 'phrases': ['включи плеер']}]
assert nlu.match('включи плеер', BEST)[0]['command'] == 'a'   # 1.00 beats 0.80

# entries without the voice fields are silent, and so is an empty phrase
assert nlu.match('терминал', [{'label': 'X', 'command': 'x'}]) is None
assert nlu.match('', ACTIONS) is None
assert nlu.match(None, ACTIONS) is None

# no actions, nothing to match
assert nlu.match('терминал', []) is None
assert nlu.match('терминал', None) is None

# pymorphy2, when it is around, folds the word forms together: «открой терминалом»
# is the whole phrase said another way (1.00), not a fuzzy 0.60
if nlu._lemmatizer():
    hit = nlu.match('открой терминалом', ACTIONS)
    assert hit and hit[0] is TERMINAL and hit[1] == nlu.SCORE_PHRASE, hit

# the whole actions.json pipeline: what load_actions keeps of the voice fields
from yumi import config  # noqa: E402
sample = _root / 'actions.example.json'
loaded = config.load_actions(sample)
assert loaded[0]['command'] == 'foot'
assert 'открой терминал' in loaded[0]['phrases']
assert 'терминал' in loaded[0]['keywords']
assert loaded[0]['speak'] == 'Открываю терминал'
assert loaded[1]['phrases'] == [] and loaded[1]['speak'] == ''   # no voice, still a button
assert nlu.match('открой терминал', loaded)[0]['command'] == 'foot'

print('tests/test_nlu.py: ok')

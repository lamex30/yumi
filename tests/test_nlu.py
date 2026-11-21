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

# ---- the structured answer: the same choice, with the rest of the meaning
VOLUME_BUTTON = {'id': 'set_volume', 'label': 'Volume', 'command': 'vol',
                 'phrases': ['громкость половина'], 'keywords': ['громкость']}
READ = [VOLUME_BUTTON, TERMINAL]
result = nlu.recognize('громкость половина', READ)
assert result is not None
assert result.action is VOLUME_BUTTON and result.intent == 'set_volume'
assert result.confidence == nlu.SCORE_PHRASE and result.slots == {}
assert result.entities['volume']['value'] == 50
assert result.as_tuple() == nlu.match('громкость половина', READ)

# the also-rans are kept for the log, but never promoted over the winner
MANY = [{'command': 'a', 'keywords': ['терминал']},
        {'command': 'b', 'phrases': ['открой терминал']},
        {'command': 'c', 'keywords': ['терминал']}]
many = nlu.recognize('открой терминал', MANY)
assert many.action['command'] == 'b'
assert [a['command'] for a, _score in many.alternatives] == ['a', 'c']

# a button's priority breaks a tie; a higher score never loses to priority
TIED = [{'command': 'a', 'phrases': ['открой терминал']},
        {'command': 'b', 'phrases': ['открой терминал'], 'priority': 5}]
assert nlu.recognize('открой терминал', TIED).action['command'] == 'b'
TIED[1]['priority'] = 0
assert nlu.recognize('открой терминал', TIED).action['command'] == 'a'
TIED[1]['keywords'] = ['терминал']
TIED[1]['phrases'] = []
assert nlu.recognize('открой терминал', TIED).action['command'] == 'a'

# the panel's line: a bent word or a keyword can be left to the chat
assert nlu.match('открой терминай', ACTIONS, min_confidence=0.9) is None
assert nlu.match('терминал', ACTIONS, min_confidence=0.9) is None
assert nlu.match('открой терминал', ACTIONS, min_confidence=0.9) is not None
assert nlu.recognize('не открывай терминал', ACTIONS, min_confidence=0.5) is None

# a bad priority is just zero, and a button without an id gets a stable one
assert nlu.recognize('открой терминал', [{'command': 'x', 'phrases': ['открой терминал'],
                                          'priority': 'many'}]).intent == 'custom:x'

# ---- the search: a url entry with {query}, the words said beside the trigger
SEARCH = {'command': 'xdg-open https://yandex.ru', 'url': 'https://yandex.ru/search/?text={query}',
          'phrases': ['найди в интернете', 'поищи в интернете', 'search the web'],
          'keywords': ['погугли', 'интернет'], 'speak': 'Ищу {query}'}
NET = [SEARCH]

# the trigger said whole, the subject after it
hit = nlu.match('найди в интернете ёжиков', NET)
assert hit and hit[1] == nlu.SCORE_PHRASE
assert hit[2] == {'query': 'ёжиков'}, hit[2]        # ё and the case stay: a search wants real words

# the subject said first, the trigger after: the keywords catch it, the verbs
# and the «в» go, wherever they stand in the phrase
hit = nlu.match('поищи котиков в интернете', NET)
assert hit and hit[1] >= nlu.MIN_SCORE            # 0.80 with pymorphy2, 0.60 without
assert hit[2] == {'query': 'котиков'}, hit[2]

# the polite words and the verbs are not the subject
hit = nlu.match('ну найди в интернете пожалуйста мне котиков', NET)
assert hit[2] == {'query': 'котиков'}, hit[2]
hit = nlu.match('search the web for pixel moths', NET)
assert hit and hit[2] == {'query': 'pixel moths'}, hit[2]

# the trigger alone carries no subject: it does not fire, the chat takes it
assert nlu.match('найди в интернете', NET) is None
assert nlu.match('погугли', NET) is None
assert nlu.match('что происходит в интернете', NET) is None

# an entry without {query} gets no slots at all
hit = nlu.match('найди в интернете ёжиков', [{'command': 'x', 'phrases': ['найди в интернете']}])
assert hit[2] == {}

# the negation holds for the search too
assert nlu.match('не ищи в интернете котиков', NET) is None

# ---- the whole actions.json pipeline: what load_actions keeps of the voice fields
from yumi import config  # noqa: E402
sample = _root / 'actions.example.json'
loaded = config.load_actions(sample)
assert loaded[0]['command'] == 'foot'
assert 'открой терминал' in loaded[0]['phrases']
assert 'терминал' in loaded[0]['keywords']
assert loaded[0]['speak'] == 'Открываю терминал'
assert loaded[1]['url'] == 'https://yandex.ru/search/?text={query}'   # the search is kept
assert loaded[2]['id'] == 'remind_tomorrow' and loaded[2]['priority'] == 2
assert loaded[2]['requires_confirmation'] is True
assert loaded[2]['entities'] == {'date': True, 'time': True}
assert '{date}' in loaded[2]['speak']
assert loaded[3]['phrases'] == [] and loaded[3]['speak'] == ''   # no voice, still a button
assert nlu.match('открой терминал', loaded)[0]['command'] == 'foot'
hit = nlu.match('найди в интернете юми мотылёк', loaded)
assert hit[0]['url'].endswith('{query}') and hit[2] == {'query': 'юми мотылёк'}, hit

print('tests/test_nlu.py: ok')

"""The tiny mind behind the rules: intents.py, the third level of nlu.match.
Run: python3 tests/test_intents.py — everything runs without the model; the
classification probes run only when tools/intents_setup.sh has trained one.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yumi import intents, nlu

ACTIONS = [
    {'label': 'Terminal', 'command': 'foot',
     'phrases': ['открой терминал'], 'keywords': ['терминал']},
    {'label': 'Play / pause', 'command': 'playerctl play-pause',
     'phrases': ['пауза'], 'keywords': ['играй']},
    {'label': 'Notes', 'command': 'gnome-text-editor'},
    {'label': 'Search', 'command': 'x', 'url': 'https://x/search?q={query}',
     'phrases': ['найди в интернете'], 'keywords': ['погугли']},
]

CORPUS = json.loads((Path(__file__).resolve().parent.parent /
                     'data/intents-corpus.json').read_text(encoding='utf-8'))

# the corpus is well-formed: labels on every entry, phrases non-empty, chat present
labels = [i['label'] for i in CORPUS['intents']]
assert 'chat' in labels, labels
assert len(set(labels)) == len(labels), 'a label twice'
for i in CORPUS['intents']:
    assert i['phrases'] and all(str(p).strip() for p in i['phrases']), i['label']
print(f"corpus: {len(labels)} labels, "
      f"{sum(len(i['phrases']) for i in CORPUS['intents'])} phrases")

# the rules stay untouched by the third level: a sure phrase needs no mind
assert nlu.match('открой терминал', ACTIONS)[0]['command'] == 'foot'

# without a model (or before the first training) the third level is silence:
# the same Nones the rules gave before
had = intents.HOME / 'model.onnx'
if not had.exists():
    assert nlu.match('запусти консольку пожалуйста', ACTIONS) is None
    assert nlu.match('привет', ACTIONS) is None
    assert nlu.match('', ACTIONS) is None
    assert nlu.match('не открывай терминал', ACTIONS) is None
    print('no model on the disk yet: silence checks done, probes skipped')
    print('tests/test_intents.py: ok')
    sys.exit(0)

intents.forget()                    # a retrain may have happened under us

# the mind answers with the nlu.match shape: (action, score, slots)
hit = nlu.match('запусти консольку пожалуйста', ACTIONS)
assert hit and hit[0]['command'] == 'foot' and 0.0 < hit[1] < 1.0, hit
assert hit[2] == {}

# the sayings it was never taught, but the meaning is clear
for said, want in (('врубь музыку', 'playerctl play-pause'),
                   ('поставь на пайзу', 'playerctl play-pause'),
                   ('мне бы терминал', 'foot'),
                   ('открой блокнотик', 'gnome-text-editor')):
    hit = nlu.match(said, ACTIONS)
    assert hit and hit[0]['command'] == want, (said, hit)

# the chat keeps the phrases that belong to it: no confident command runs on them
for said in ('какая сегодня погода', 'расскажи анекдот', 'привет юми',
             'сколько будет два плюс два'):
    assert nlu.match(said, ACTIONS) is None, said

# the phrases the mind was never taught and that look like commands but are
# not her buttons: the cosine guard keeps them out of the commands
for said in ('открой окно', 'запусти игру', 'включи свет', 'открой шторы'):
    assert nlu.match(said, ACTIONS) is None, said

# the negaters and the empty phrases never reach the mind
assert nlu.match('не открывай терминал', ACTIONS) is None
assert nlu.match('', ACTIONS) is None
assert nlu.match(None, ACTIONS) is None

# a url with {query} is not offered to the mind (the slots are the rules' craft):
# a bare «погугли» already falls through by the rules
hit = nlu.match('найди в интернете котиков', ACTIONS)
assert hit and hit[2].get('query') == 'котиков'    # the rules' answer, score 1.00
assert nlu.match('погугли', ACTIONS) is None

# nothing is offered: no buttons, no mind
assert nlu.match('привет', []) is None
assert nlu.match('привет', None) is None

print('tests/test_intents.py: ok')

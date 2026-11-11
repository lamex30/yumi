#!/usr/bin/env python3
"""She hears her name through Vosk's hear-say. Run: python3 tests/test_wake.py"""
import importlib.util
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('stt_daemon', _root / 'yumi' / 'stt_daemon.py')
stt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stt)

spellings = stt.variants('юми') | stt.hearsay_load('юми')
assert stt.variants('юми') == {'юми', 'yumi', 'umi'}, stt.variants('юми')

# the plain hearsay the small models write for her name
for said in ('юми', 'а юми', 'ну, юми включи таймер', 'yumi what time is it',
             'umi open the shelf', 'ю и ми', 'у ми прив', 'дэм юми'):
    assert stt.wake_at(said, spellings)[0] is not None, said

# the hearsay from the ears' own diary: what Vosk really writes for «юми»
for said in ('юме погода', 'юмей расскажи анекдот', 'юный погода', 'еми погода',
             'еле погода', 'ими', 'ями', 'юме время', 'а можно ми погода',
             'эпитет да юми', 'виноват пусть и юный погода', 'письма юме погода',
             'нормальный цен юный погоду на завтра'):
    assert stt.wake_at(said, spellings)[0] is not None, said

# what is left after the name goes to her mind
assert stt.wake_at('юми, включи таймер', spellings) == ('включи таймер', 'юми')
assert stt.wake_at('ну юми включи', spellings) == ('включи', 'юми')
assert stt.wake_at('ю и ми откры', spellings) == ('откры', 'юими')
assert stt.wake_at('а юми', spellings) == ('', 'аюми')
assert stt.wake_at('виноват пусть и юный погода', spellings) == ('погода', 'июный')

# not her name: whole glued-up words, words one look-alike away, or simply other ones
for said in ('юмизнает', 'конюми', 'таймер', 'привет мир', 'включи свет',
             'юмор сегодня', 'юбилей отметила', 'объем молока'):
    assert stt.wake_at(said, spellings) == (None, None), said

# near(): one letter for short words, two for the long ones, never the length gap
assert stt.near('уми', 'юми')
assert not stt.near('дни', 'юми')
assert not stt.near('привет', 'приветмир')
assert not stt.near('записка', 'заметка')          # three slips and it is another word
assert stt.near('приветсур', 'приветмир')          # two slips in a long word are ok

# the learning: a spelling one letter from the name gets remembered for next time,
# but a sound two slips away (a video's word) must not grow into the name
_hearsay = stt.HEARSAY_FILE
stt.HEARSAY_FILE = Path(_root / 'tests' / 'hearsay.tmp.json')
if stt.HEARSAY_FILE.exists():
    stt.HEARSAY_FILE.unlink()
stt.hearsay_save('юми', 'дюме')
stt.hearsay_save('юми', 'дюме')                    # twice said, never remembered at all
learned = lambda: stt.hearsay_load('юми') - set(stt.HEARSAY['юми'])
assert not learned()
stt.hearsay_save('юми', 'оми')
stt.hearsay_save('юми', 'оми')                     # one slip from the name: remembered once
assert learned() == {'оми'}
stt.hearsay_save('юми', 'ю ми')                    # a two-word slip is not a spelling
data = stt.hearsay_load('юми')
assert 'ю ми' not in data                          # hear-say is one token, not two
assert 'оми' in data
stt.HEARSAY_FILE.unlink()
stt.HEARSAY_FILE = _hearsay

# her name deep inside a stream of other words does not wake her: a name is said first
assert stt.wake_at('привет как дела сегодня погода хорошая совсем неплохо ну юми',
                   spellings) == (None, None)

print('test_wake: ok')
sys.exit(0)

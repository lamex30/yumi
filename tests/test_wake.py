#!/usr/bin/env python3
"""She hears her name through Vosk's hear-say. Run: python3 tests/test_wake.py"""
import importlib.util
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('stt_daemon', _root / 'yumi' / 'stt_daemon.py')
stt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stt)

spellings = stt.variants('юми')
assert spellings == {'юми', 'yumi', 'umi'}, spellings

# the plain hearsay the small models write for her name
for said in ('юми', 'а юми', 'ну, юми включи таймер', 'yumi what time is it',
             'umi open the shelf', 'ю и ми', 'у ми прив', 'дэм юми'):
    assert stt.wake_at(said, spellings) is not None, said

# what is left after the name goes to her mind
assert stt.wake_at('юми, включи таймер', spellings) == 'включи таймер'
assert stt.wake_at('ну юми включи', spellings) == 'включи'
assert stt.wake_at('ю и ми откры', spellings) == 'откры'
assert stt.wake_at('а юми', spellings) == ''

# not her name: whole glued-up words, words one look-alike away, or simply other ones
for said in ('юмизнает', 'конюми', 'таймер', 'привет мир', 'включи свет'):
    assert stt.wake_at(said, spellings) is None, said

# near(): one letter for short words, two for the long ones, never the length gap
assert stt.near('уми', 'юми')
assert not stt.near('дни', 'юми')
assert not stt.near('привет', 'приветмир')
assert not stt.near('записка', 'заметка')          # three slips and it is another word
assert stt.near('приветсур', 'приветмир')          # two slips in a long word are ok

print('test_wake: ok')
sys.exit(0)

#!/usr/bin/env python3
"""«Включи музыку» plays her library, it never toggles someone else's player.
Run: python3 tests/test_music_text.py"""
import importlib.util
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
from yumi import brain  # noqa: E402

m = brain.music_control_text

# her own phrases from the ears' diary, 17:54, the evening it all went wrong
assert m('юми включи музыку') == 'play'
assert m('включи музыку') == 'play'
assert m('музыку') == 'play'                      # said alone, patiently, the second time
assert m('юми музыку') == 'play'

# what the telly murmured in the background: none of it is hers to mind
for said in ('только программы', 'поход', 'палаток пора попы попа', 'про рок',
             'аппарат какое-то по выключи и не включила музыку'):
    assert m(said) is None, said

# not her business either, even with a verb around
for said in ('включи свет', 'переключи канал', 'не включай музыку', 'хватит музыки'):
    assert m(said) is None, said

# the rest of the skill: whole words, not substrings
assert m('пауза') == 'pause'
assert m('на паузу') == 'pause'
assert m('pause') == 'pause'
assert m('выключи музыку') == 'stop'
assert m('turn off the music') == 'stop'
assert m('стоп') == 'stop'
assert m('следующий трек') == 'next'
assert m('переключи на следующий') == 'next'
assert m('переключи музыку') == 'next'
assert m('предыдущий') == 'previous'
assert m('назад') == 'previous'
assert m('rewind') == 'previous'
assert m('play music') == 'play'
assert m('запусти плейлист') == 'play'
assert m('') is None
assert m('юми') is None

# the volume, the way the ears really hear it: падежи and a lone number count
assert m('юми громкость тридцать') == 'volume 30'
assert m('громкости 30') == 'volume 30'            # whisper's verdict from the diary
assert m('громкость девяносто') == 'volume 90'
assert m('звук на пятьдесят') == 'volume 50'
assert m('громкость пять') == 'volume 50'          # the tens she obeys
assert m('30') is None                             # a bare number is a wish only behind her name

# a lone number after the name is a volume wish too (Brain.heard_text, wake=True)
assert brain.bare_volume('30') == 30
assert brain.bare_volume('тридцать') == 30
assert brain.bare_volume('пять') == 50
assert brain.bare_volume('сколько два плюс два') is None
assert brain.bare_volume('громкость тридцать') is None   # not alone: the parser's job
print('ok')

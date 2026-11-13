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
assert m('включи следующий трек') == 'next'
assert m('предыдущий') == 'previous'
assert m('назад') == 'previous'
assert m('rewind') == 'previous'
assert m('play music') == 'play'

# «дальше»: her word for the next one, and the ears' slips over the playing music
assert m('дальше') == 'next'
assert m('юми дальше') == 'next'
assert m('дальш') == 'next'                       # a letter the music ate
assert m('перемотай дальше') == 'next'
assert m('продолжай дальше') == 'play'            # a resume, not a skip
assert m('следуший') == 'next'                    # the same slip in «следующий»
assert m('предъидущий') == 'previous'
assert m('запусти плейлист') == 'play'
assert m('') is None
assert m('юми') is None

# «кто играет»: a question about the song, never a wish to start one
assert m('кто играет') == 'who'
assert m('что играет') == 'who'
assert m('что сейчас играет') == 'who'
assert m('что за трек') == 'who'
assert m('какая песня играет') == 'who'
assert m('какой трек играет') == 'who'
assert m("what's playing") == 'who'
assert m('what song is this') == 'who'
assert m('юми кто играет') == 'who'
assert m('что включи музыку') == 'play'           # a verb nearby: it is a wish, not a question
assert m('что выключи музыку') is None            # the stray «что» is a stranger to the parser

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

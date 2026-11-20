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

# the wide pre-filter (the judge's budget) must not eat the word after the name:
# «юми кто играет» must not lose «кто» to a glued «юмикто»
assert stt.wake_at('юми кто играет', spellings, 3) == ('кто играет', 'юми')
assert stt.wake_at('юный кто играет', spellings, 3) == ('кто играет', 'юный')
assert stt.wake_at('юми твои параметры', spellings, 3) == ('твои параметры', 'юми')
assert stt.wake_at('юный твои параметры', spellings, 3) == ('твои параметры', 'юный')
assert stt.wake_at('юми, то играй', spellings, 3) == ('то играй', 'юми')
# the near-miss the wide budget exists for still reaches the judge
assert stt.wake_at('юноша включи музыку', spellings, 3) == ('включи музыку', 'юноша')

# not her name: whole glued-up words, words one look-alike away, or simply other ones
for said in ('юмизнает', 'конюми', 'таймер', 'привет мир', 'включи свет',
             'юмор сегодня', 'юбилей отметила', 'объем молока'):
    assert stt.wake_at(said, spellings) == (None, None), said

# near(): one letter for short words, two for the long ones, never the length gap
# now returns the edit distance (0 = identical), or lim+1 when over budget
assert stt.near('уми', 'юми') == 1
assert stt.near('юми', 'юми') == 0
assert stt.near('дни', 'юми') > 1
assert stt.near('привет', 'приветмир') > 1       # length gap over budget
assert stt.near('записка', 'заметка') > 2         # three slips and it is another word
assert stt.near('приветсур', 'приветмир') <= 2    # two slips in a long word are ok

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

# stop_at: «стоп» overrides everything, and a one-letter slip over the music still counts
assert stt.stop_at('стоп')
assert stt.stop_at('стопи')
assert stt.stop_at('остановись')
assert stt.stop_at('юми стоп')
assert stt.stop_at('стол')            # the ears' slip over the playing music
assert stt.stop_at('стой')
assert not stt.stop_at('включи музыку')
assert not stt.stop_at('')
assert not stt.stop_at('столько')     # two slips and more: not a stop
assert not stt.stop_at('стопор')

# stop_shout: through the closed ears (her voice is playing) only a stop and nothing
# else may interrupt her — her own «останови» inside a longer answer never counts
assert stt.stop_shout('стоп')
assert stt.stop_shout('стопи')
assert stt.stop_shout('остановись')
assert stt.stop_shout('стол')                     # the ears' slip over the music
assert stt.stop_shout('юми стоп', spellings)
assert stt.stop_shout('юный стоп', spellings)     # the hear-say the ears really write
assert not stt.stop_shout('стоп так нельзя')      # her own words never stop her
assert not stt.stop_shout('остановилась')
assert not stt.stop_shout('скажи стоп')
assert not stt.stop_shout('')
assert not stt.stop_shout('привет')

# the mute: her ears close while she speaks and open again when she is stopped
stt.mute_for(5)
assert stt.muted()
stt.mute_for(2)                       # a fresh call only stretches the window
assert stt.muted()
stt.mute_for(0)                       # her voice stopped early: the ears open
assert not stt.muted()

# the trained ear's window: a louder frame takes the top at once, quiet ones keep
# the peak for a while, and when the window runs out even a quiet frame replaces it —
# so a silence after speech reads as silence, and vosk's dreams die at the veto
w = stt.ear_window_hi
hi, t = w(0.0, 0.0, 0.3, 1.0)
assert (hi, t) == (0.3, 1.0)
assert w(hi, t, 0.1, 2.0) == (0.3, 1.0)      # within the window the peak stands
assert w(hi, t, 0.9, 2.0) == (0.9, 2.0)      # a sure name takes the top at once
assert w(0.9, 1.0, 0.1, 5.0) == (0.1, 5.0)   # the window ran out: silence is silence
assert stt.OWW_VETO < stt.OWW_THRESHOLD      # the veto sits under the ear's own doubt

# after_name: the one verdict on every moment after the name. The (0, 400) ms
# breath — the name's own tail — used to satisfy neither the greeting nor the
# endpoint, and the wake then sat mute until the user spoke again or the window
# timed out (15–120 s stalls in the logs)
a = stt.after_name
# only the name stood after it, the quiet came: her turn to greet
assert a(0.0, 1.6, 3.0, 15.0) == 'greet'
assert a(250.0, 1.6, 3.0, 15.0) == 'greet'        # a breath: still the name alone
# the words may still be coming: keep listening
assert a(0.0, 0.3, 3.0, 15.0) is None             # quiet has not settled yet
assert a(250.0, 0.3, 3.0, 15.0) is None
assert a(900.0, 0.3, 3.0, 15.0) is None           # a real phrase, mid-flight
# voice, then VAD_TAIL of quiet: the phrase is over
assert a(900.0, 1.3, 3.0, 15.0) == 'end'
assert a(stt.PHRASE_MIN, 1.3, 3.0, 15.0) == 'end'  # 400 ms is a phrase already
assert a(900.0, stt.VAD_TAIL / 1000.0 - 0.1, 3.0, 15.0) is None  # shorter than the tail
# the listen ran out: whatever gathered goes to the ear, this wake waits no longer
assert a(900.0, 0.3, 16.0, 15.0) == 'ceiling'
assert a(100.0, 0.3, 16.0, 15.0) == 'ceiling'      # reachable in the (0, 400) hole too
assert a(0.0, 0.3, 16.0, 15.0) == 'ceiling'        # and with nothing on the tape
assert a(900.0, 1.3, 16.0, 15.0) == 'end'          # a finished phrase still ends first

print('test_wake: ok')
sys.exit(0)

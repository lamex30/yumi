#!/usr/bin/env python3
"""The arbitrator and the judge's doorman. Run: python3 tests/test_arbiter.py

The wake's decision points are tested apart from the microphone: the trained
ear's window (EarStat), the middle mind (Arbiter.decide — the old EARVETO is a
rank now, not a veto), the doorman (Arbiter.may_send — no endless judge, no
same fragment twice), the phonetic net (phon_candidate), the judge's fragment
cut from the ring buffer, the doubts' diary (Diag) and the fourteen scenarios
of the terms of reference, each with the ears' answers given from the tests.
"""
import importlib.util
import json
import sys
import tempfile
import time
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('stt_daemon', _root / 'yumi' / 'stt_daemon.py')
stt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stt)

# fixed dials, whatever the running settings.json says: the tests judge the
# defaults, not the user's tuning
stt.TUNE.update({'oww_threshold': 0.5, 'oww_sure': 0.7, 'oww_hold': 0.35,
                 'oww_veto': 0.2, 'oww_window': 3.0, 'oww_spike': 2,
                 'judge_tape': 5.0, 'judge_rate': 3.0, 'judge_ttl': 10.0,
                 'wake_phonetic': True, 'stt_diag_wav': False})

spellings = stt.variants('юми') | stt.hearsay_load('юми')

# ---- EarStat: the window, the hysteresis, the spike protection ----
t = 100.0
ear = stt.EarStat()
s = ear.feed(0.6, t)
assert s['cur'] == 0.6 and s['hi'] == 0.6 and s['streak'] == 1, s
assert not s['active'], 'one frame is a spike: no candidate yet'
s = ear.feed(0.6, t + 0.08)                       # a stretch, not a spike
assert s['active'] and s['streak'] == 2, s
assert round(s['pos_ms']) == 160, s               # 2 frames × 80 ms above the line
s = ear.feed(0.4, t + 0.16)                       # a dip between the syllables
assert s['active'], 'the hold line keeps the candidate'
assert abs(s['mean'] - (0.6 + 0.6 + 0.4) / 3) < 1e-9, s
s = ear.feed(0.1, t + 0.24)                       # below the hold line: it is over
assert not s['active'], s
ear.reset()
assert not ear.feed(0.9, t)['active'], 'one loud frame is a knock, not a name'
assert ear.feed(0.9, t + 0.08)['active'], 'a stretch is believed'
# a dip above the hold line does not break the count of the stretch
ear.reset()
ear.feed(0.55, t)
s = ear.feed(0.38, t + 0.08)
assert s['streak'] == 1, s                        # the dip is tolerated
assert ear.feed(0.55, t + 0.16)['active'], s      # and the stretch continues
for _ in range(50):                               # the window slides: the old frames go
    ear.feed(0.9, t)
    t += 0.08
s = ear.feed(0.0, t + 5.0)
assert s['hi'] == 0.0 and not s['active'], 'a silence after speech reads as silence'

# ---- Arbiter.decide: the old absolute veto is a rank now ----
a = stt.Arbiter()
# a sure ear (a stretch, the current frame at the sure line) or a whole
# spelling: past everything
assert a.decide({'hi': 0.8, 'cur': 0.8, 'streak': 2, 'active': True},
                False, False, False, False)[0] == stt.ARB_FAST
# one loud frame alone is what a video does too: no fast wake, the ear's rank
# only — the next frames must hold the stretch for the judge or the wake
assert a.decide({'hi': 0.8, 'cur': 0.8, 'streak': 1, 'active': False},
                False, False, False, False) == (stt.ARB_IGNORE, 'ear_low')
assert a.decide({'hi': 0.0}, True, True, False, True) == (stt.ARB_FAST, 'name_exact')
# 0.5–0.7, held by the hysteresis: to the judge
assert a.decide({'hi': 0.55, 'cur': 0.55, 'streak': 2, 'active': True},
                False, False, False, True) == \
    (stt.ARB_JUDGE, 'ear_strong')
# 0.2–0.5 + a voice + a vosk candidate: to the judge; without the voice: no
assert a.decide({'hi': 0.3, 'active': False}, False, True, False, True) == \
    (stt.ARB_JUDGE, 'ear_low_cand')
assert a.decide({'hi': 0.3, 'active': False}, False, True, False, False)[0] == stt.ARB_IGNORE
# below the deaf line: a name-shaped sound still reaches the judge, a bare
# moment does not — the veto demoted, not silenced
assert a.decide({'hi': 0.1, 'active': False}, False, False, True, False) == \
    (stt.ARB_JUDGE, 'phonetic')
assert a.decide({'hi': 0.1, 'active': False}, False, True, False, True, True) == \
    (stt.ARB_JUDGE, 'vosk_close')
assert a.decide(None, False, True, False, True, True) == (stt.ARB_JUDGE, 'vosk_close')
# a far mush («мечии», three letters from the name) is ordinary speech: no judge
assert a.decide({'hi': 0.1, 'active': False}, False, True, False, True, False) == \
    (stt.ARB_IGNORE, 'deaf')
assert a.decide({'hi': 0.1, 'active': False}, False, False, False, False) == \
    (stt.ARB_IGNORE, 'deaf')

# ---- the doorman: no endless judge, no same fragment twice ----
a = stt.Arbiter()
frag = b'\x01\x02' * 100
assert a.may_send('юме', frag, 10.0, False) == (True, 'ok')
assert a.may_send('юме', frag, 11.0, False)[1] == 'rate'      # the window between reads
assert a.may_send('юме', frag, 14.0, False)[1] == 'same'      # the very same fragment
assert a.may_send('юме', b'\x03' * 200, 14.0, True)[1] == 'busy'
ok, _ = a.may_send('юме', frag + b'\x00' * 10, 14.0, False)   # new sound on the tape
assert ok, 'a fresh fragment is not the one the judge already read'
assert a.sent == 2 and a.held == 3, (a.sent, a.held)

# ---- the phonetic net: the slips the letter budget missed ----
# (the one-letter slips are the wake budget's own catch: дюми, уми, юмми)
for said in ('дюми', 'уми', 'юмми'):
    assert stt.wake_at(said, spellings)[0] is not None, said
for said in ('умы на улице', 'юми-и включи свет'):
    hit, said_back = stt.phon_candidate(said, spellings)
    assert hit is not None, said
# the stop words never dress up as the name, and the ordinary words keep out
for said in ('иди сюда', 'уже поздно', 'привет как дела', 'включи музыку', 'умер'):
    assert stt.phon_candidate(said, spellings) == (None, None), said
assert stt.phon_key('юмми') == stt.phon_key('юми') == 'уми'
assert stt.phon_key('умы') == 'уми'

# ---- the wake match keeps its own tests green and gains the new hear-says ----
assert stt.wake_at('юмми включи свет', spellings)[0] == 'включи свет'
assert stt.wake_at('юми-и привет', spellings)[0] == 'привет'

# ---- the judge's fragment out of the ring buffer ----
rate = stt.RATE * 2
tape = bytes(range(256)) * (stt.WAKE_TAPE // 256)          # the whole 8 s shelf
n = min(len(tape), int(stt.TUNE['judge_tape'] * rate))
frag = tape[len(tape) - n:]
assert len(frag) == 5 * rate and frag == tape[-5 * rate:], 'the fragment is the newest sound'
assert stt.phon_key('юми') in ('уми',)

# ---- the diary: one jsonl line per doubt, and the debug wavs when asked ----
tmp = Path(tempfile.mkdtemp())
stt.TAP_DIR = tmp
d = stt.Diag(True)
d.event(vad=True, ear_hi=0.31, vosk_text='что', wake_candidate=True,
        arbitration='SEND_JUDGE', judge_called=True, final_wake=False)
lines = (tmp / 'wake.jsonl').read_text(encoding='utf-8').strip().splitlines()
assert len(lines) == 1 and 'timestamp' in json.loads(lines[0]), lines
stt.Diag(False).event(vad=True)                            # the off diary writes nothing
assert len((tmp / 'wake.jsonl').read_text(encoding='utf-8').strip().splitlines()) == 1

stt.TUNE['stt_diag_wav'] = True
stt.judge_wav(frag[:rate])
stt.TUNE['stt_diag_wav'] = False
assert list(tmp.glob('judge-*.wav')), 'the debug wav missed the shelf'

# ---- the fourteen scenarios, each with the ears' answers given ----
def moment(ear_scores, words, vad=True, exact=None, mute=False, muted_now=False,
           cand=None, phon=None):
    """one moment of the wake, the way the wake loop would weigh it: the ear's
    scores are fed one by one, vosk's words are given, and the arbitrator says
    where the moment goes. ear_scores: [] = no trained ear at all."""
    stat = None
    base = t
    for i, s in enumerate(ear_scores):
        if stat is None:
            stat = stt.EarStat()
        stat.feed(s, base + i * 0.08)
    if cand is None:
        hit, src = stt.wake_at(words, spellings, 3) if words else (None, None)
        phonetic = hit is None and bool(words)
        if phonetic:
            hit, src = stt.phon_candidate(words, spellings)
        cand = hit is not None
        phon = bool(cand and phonetic)
    if exact is None:
        exact = bool(words) and stt.name_exact(words, spellings)
    close = bool(cand and (exact or phon or
                           min(stt.near(src, s, 2) for s in spellings) <= 1))
    if mute or muted_now:
        # her own voice plays: the wake detector is shut, the stop still works
        if muted_now:
            assert stt.muted()
        if words and stt.stop_shout(words, spellings):
            return 'MUTED_STOP'
        return 'MUTED'
    action, why = stt.Arbiter().decide(stat, exact, cand, phon, vad, close)
    return action


# 1. a clear «юми»: past everything, no judge needed
assert moment([0.3, 0.3], 'юми включи музыку') == stt.ARB_FAST
# 2. a quiet «юми»: vosk writes mush («юмни»), but the ear's stretch stands — judge
assert moment([0.5, 0.55, 0.55], 'юмни погода') == stt.ARB_JUDGE
# 3. from across the room: a low ear, a voice, a wide-budget near-name — judge
assert moment([0.3], 'юноша включи музыку') == stt.ARB_JUDGE
# 4. over the music: a deaf ear, a verbatim known hear-say — she wakes as before
assert moment([0.1], 'юный погода') == stt.ARB_FAST
# 5. «юный», the hear-say the ears really write over music
assert stt.wake_at('юный погода', spellings)[0] == 'погода'
# 6. vosk dreams the name into another word: «уме» is a known hear-say, the judge
assert stt.wake_at('уме сказал', spellings)[0] == 'сказал'
assert moment([0.1], 'уме сказал') == stt.ARB_FAST
# 7. a low oww score, a phonetic candidate the budget missed («умы» for «юми»)
assert moment([0.1], 'умы на улице') == stt.ARB_JUDGE
# 8. a high score on a stranger's noise: the ear asks, the judge reads — and
# the doorman's window keeps it from reading the same noise over and over
assert moment([0.5, 0.55, 0.6], '') == stt.ARB_JUDGE
a = stt.Arbiter()
assert a.may_send('', b'noise', 100.0, False)[0]
assert not a.may_send('', b'noise', 101.0, False)[0]
# 9. a video saying the name: the same fragment is not read twice
a = stt.Arbiter()
assert a.may_send('юме', b'video', 100.0, False)[0]
assert a.may_send('юме', b'video', 105.0, False)[1] == 'same'
# 9b. a video's ordinary speech («мечии», «ему»-like far mush): no judge at all —
# the far mush is not a confident candidate, the ear is deaf and nothing else speaks
assert moment([0.1], 'давайте задачку едва мечи и') == stt.ARB_IGNORE
assert moment([0.3], 'давайте задачку едва мечи и') == stt.ARB_JUDGE
# 10. she speaks herself: the ears are shut to her own words
stt.mute_for(5)
assert stt.muted()
assert moment([0.8], 'юми юми', mute=True) == 'MUTED'
# 11. «стоп» through her own voice: it cuts through
assert stt.stop_shout('стоп', spellings) and stt.stop_at('стоп')
assert not stt.stop_shout('скажи стоп', spellings)
# 12. a call right after her voice stopped: the mute lifted, the ears hear
stt.mute_for(0)
assert not stt.muted()
assert moment([0.3, 0.3], 'юми кто тут') == stt.ARB_FAST
# 13. the name alone, no command: she wakes and waits
assert stt.wake_at('юми', spellings) == ('', 'юми')
assert stt.name_exact('юми', spellings)
# 14. a long command after the name: the whole tail goes to her mind
assert stt.wake_at('юми поставь таймер на десять минут и включи музыку',
                   spellings) == ('поставь таймер на десять минут и включи музыку', 'юми')

print('test_arbiter: ok')
sys.exit(0)

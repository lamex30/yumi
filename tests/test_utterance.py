"""The phrase on tape for whisper: the lead before the first voice, the whole phrase
after it, nothing of the room hiss. Run: python3 tests/test_utterance.py"""
import importlib.util
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('stt_daemon', _root / 'yumi' / 'stt_daemon.py')
stt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stt)

# no voice yet: only the newest LEAD bytes stay, the room hiss before them dies
u = stt.Utterance()
for _ in range(10):
    u.feed(b'\x00' * 4000, False)                  # 10 chunks of quiet: far over the lead
assert len(u.lead) == stt.Utterance.LEAD, len(u.lead)
assert u.take() == b'', 'silence is not a phrase: whisper gets nothing'

# the voice arrives: the lead goes in front, the phrase and its inner quiet follow
speech = b'\x01' * 4000
hush = b'\x02' * 4000
u = stt.Utterance()
u.feed(b'\x00' * 5000, False)                      # quiet (the last LEAD bytes stay)
u.feed(speech, True)                               # the first voice
u.feed(hush, False)                                # the quiet inside the phrase stays
assert u.take() == b'\x00' * 5000 + speech + hush, 'the phrase lost its head'

# take() clears the tape: the next phrase collects from scratch
assert u.take() == b''

# the quiet alone after the voice ended still counts: the tail of the phrase is its own
u = stt.Utterance()
u.feed(speech, True)
u.feed(b'\x00' * 9000, False)                      # the VAD tail, over the lead
tape = u.take()
assert tape.startswith(speech) and len(tape) == len(speech) + 9000, tape

print('test_utterance: ok')
sys.exit(0)

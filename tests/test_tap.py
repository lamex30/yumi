"""The ears' diary bits: the recogn line, the ear range and the tap's wish on disk.
Run: python3 tests/test_tap.py"""
from array import array
import importlib.util
import sys
import tempfile
import time
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('stt_daemon', _root / 'yumi' / 'stt_daemon.py')
stt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stt)

# tap_line(): «time - phrase», the very shape recogn.txt keeps
line = stt.tap_line('юми который час', now=time.mktime((2026, 11, 11, 15, 48, 3, 0, 0, -1)))
assert line == '15:48:03 - юми который час', line
assert stt.tap_line('').endswith(' - '), 'an empty phrase still draws its stamp'

# the ear range: the same murmur dies by the «close» gate and passes the «far» one
murmur = array('h', [500] * 960).tobytes()            # rms 500: a voice from the far corner
for ear, heard in (('near', False), ('mid', True), ('far', True)):
    gmin, gk = stt.ear_gate(ear)
    st = {'floor': 50.0, 'gate_min': gmin, 'gate_k': gk}
    out = stt.gate_noise(murmur, st, False)
    assert (out == murmur) == heard, (ear, 'the murmur must not sneak past the gate')

# the plain gate keeps its old shape when the range is not named
out = stt.gate_noise(murmur, {'floor': 50.0}, False)
assert out == murmur, 'the default ear must stay exactly as it always was'

# the diary is a ring: only the newest segments stay on the shelf
with tempfile.TemporaryDirectory() as tmp:
    old_tap, stt.TAP_DIR = stt.TAP_DIR, Path(tmp)
    try:
        for i in range(7):                            # seven old tapes, in name order
            (Path(tmp) / f'ears-2026111{ i }0-000000.wav'.replace(' ', '')).write_bytes(b'')
        stt.Tap(object())._prune()
        left = sorted(p.name for p in Path(tmp).glob('ears-*.wav'))
        assert len(left) == 5, left                   # two oldest are gone
        assert left[0] == 'ears-202611120-000000.wav'  # the third oldest is now the first
    finally:
        stt.TAP_DIR = old_tap

# recogn.txt is a rolling window: the freshest 1000 lines, the old ones go first
with tempfile.TemporaryDirectory() as tmp:
    book = Path(tmp) / 'recogn.txt'
    with open(book, 'w', encoding='utf-8') as f:
        for i in range(stt.TAP_LINES + 5):            # 1005 lines, «line 0» the oldest
            f.write(f'line {i}\n')
    assert stt.tap_roll(book, stt.TAP_LINES + 5) == stt.TAP_LINES
    with open(book, encoding='utf-8') as f:
        kept = f.read().splitlines()
    assert len(kept) == stt.TAP_LINES                 # no more than a thousand lines
    assert kept[0] == 'line 5' and kept[-1] == 'line 1004'   # the oldest five are gone
    assert stt.tap_roll(Path(tmp) / 'nowhere', 42) == 42      # a missing file changes nothing

# the tap's wish outlives the daemon on disk, and «off» leaves nothing to resume
with tempfile.TemporaryDirectory() as tmp:
    old, stt.STATE = stt.STATE, Path(tmp) / 'stt' / 'port.json'
    try:
        assert stt.load_tap_state() is None            # never asked yet
        stt.save_tap_state(True, {'mic': 'mic0', 'lang': 'ru', 'size': 'big'})
        assert stt.load_tap_state() == {'mic': 'mic0', 'lang': 'ru', 'size': 'big',
                                        'ear': 'mid'}
        stt.save_tap_state(True, {'mic': None, 'lang': 'ru', 'size': 'odd', 'ear': 'near'})
        assert stt.load_tap_state() == {'mic': None, 'lang': 'ru', 'size': 'small',
                                        'ear': 'near'}
        stt.save_tap_state(False)
        assert stt.load_tap_state() is None            # off: the daemon must not resume it
    finally:
        stt.STATE = old

print('test_tap: ok')
sys.exit(0)

"""The ears' diary bits: the recogn line and the tap's wish on disk.
Run: python3 tests/test_tap.py"""
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

# the tap's wish outlives the daemon on disk, and «off» leaves nothing to resume
with tempfile.TemporaryDirectory() as tmp:
    old, stt.STATE = stt.STATE, Path(tmp) / 'stt' / 'port.json'
    try:
        assert stt.load_tap_state() is None            # never asked yet
        stt.save_tap_state(True, {'mic': 'mic0', 'lang': 'ru', 'size': 'big'})
        assert stt.load_tap_state() == {'mic': 'mic0', 'lang': 'ru', 'size': 'big'}
        stt.save_tap_state(True, {'mic': None, 'lang': 'ru', 'size': 'odd'})
        assert stt.load_tap_state() == {'mic': None, 'lang': 'ru', 'size': 'small'}
        stt.save_tap_state(False)
        assert stt.load_tap_state() is None            # off: the daemon must not resume it
    finally:
        stt.STATE = old

print('test_tap: ok')
sys.exit(0)

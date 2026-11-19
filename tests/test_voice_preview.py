#!/usr/bin/env python3
"""The panel's «listen to the voice» sample: it has no bubble of its own, so the
frame loop must not hush it like her ordinary speech. Run: python3 tests/test_voice_preview.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yumi import config  # noqa: E402
from yumi.ui import bridge as bridge_mod  # noqa: E402
from yumi.ui import senses as senses_mod  # noqa: E402


class Voice(senses_mod.SensesMixin):
    def __init__(self):
        self.voice_proc = None
        self.voice_preview = False


class Proc:
    def __init__(self):
        self.terminated = False

    def terminate(self):
        self.terminated = True


played = []
proc = Proc()
real_play = senses_mod.system.play_file
senses_mod.system.play_file = lambda path: played.append(path) or proc

v = Voice()
assert v.play_voice_file('/tmp/sample.wav') is True
assert played == ['/tmp/sample.wav'] and v.voice_preview is True, (played, v.voice_preview)
v.hush()
assert v.voice_proc is None and v.voice_preview is False, 'hush must clear the preview flag'
assert proc.terminated, 'hush must stop the player'

senses_mod.system.play_file = lambda path: False
assert v.play_voice_file('/tmp/sample.wav') is False
assert v.voice_preview is False, 'no player: no preview to protect'
senses_mod.system.play_file = real_play


class App:
    def __init__(self):
        self.settings = dict(config.DEFAULT_SETTINGS)
        self.played = []
        self.play_ok = True

    def _web_call(self, fn, _timeout=30):
        return fn()

    def play_voice_file(self, path):
        self.played.append(path)
        return self.play_ok


real_sync, real_speak = bridge_mod.tts.sync_profile, bridge_mod.tts.speak
bridge_mod.tts.sync_profile = lambda _s: {'changed': False, 'profile': {'pitch': 1.12}}
bridge_mod.tts.speak = lambda _text, _vol: Path('/tmp/sample.wav')

app = App()
web = bridge_mod.WebBridge(app)
out = web.voice_test('Привет', 'ru')
assert out['playing'] is True and app.played == [Path('/tmp/sample.wav')], out

app.play_ok = False
try:
    web.voice_test('Привет', 'ru')
except ValueError as exc:
    assert 'audio player' in str(exc), exc
else:
    raise AssertionError('a sample that never started must be an error, not a silent success')

bridge_mod.tts.sync_profile, bridge_mod.tts.speak = real_sync, real_speak
print('ok')

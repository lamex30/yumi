#!/usr/bin/env python3
"""A bare «стоп» from the wake ear must reach the brain: the App has no stop_all
of its own (the brain does), so the wake loop has to call it through ev —
«GLib.idle_add(self.stop_all)» died quietly in the idle callback and the music
played on. Run: python3 tests/test_stop_wake.py"""
import re
import sys
from pathlib import Path

src = (Path(__file__).resolve().parent.parent / 'yumi' / 'ui' / 'senses.py').read_text(encoding='utf-8')

m = re.search(r"if res\.get\('stop'\):\n(.*?)\n(\s*)continue", src, re.S)
assert m, "the wake loop lost its stop branch"

body = m.group(1)
assert 'self.stop_all' not in body, \
    'self.stop_all does not exist on the App: bare «стоп» dies in the idle callback'
assert 'b.stop_all' in body, \
    'the stop must land in the brain, the same stop_all «юми стоп» goes through'

print('test_stop_wake: ok')
sys.exit(0)

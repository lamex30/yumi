#!/usr/bin/env python3
"""The X11 pipette turns a raw pixel into '#rrggbb' by the visual's channel masks.
Run: python3 tests/test_x11_pixel.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from yumi.x11 import pixel_hex  # noqa: E402

assert pixel_hex(0x00bea7c8, 0xff0000, 0xff00, 0xff) == '#bea7c8'        # 24/32-bit
assert pixel_hex(0xffbea7c8, 0xff0000, 0xff00, 0xff) == '#bea7c8'        # alpha byte ignored
assert pixel_hex(0xf800, 0xf800, 0x07e0, 0x001f) == '#ff0000'            # 16-bit 565
assert pixel_hex(0x3ff << 20, 0x3ff << 20, 0x3ff << 10, 0x3ff) == '#ff0000'  # 30-bit
print('ok')

"""Her voice: little 8-bit blips while her words type out, one per letter (no GTK here).

A whole line is rendered into one short WAV (blips at the moments the letters appear), so one
player process speaks the line and stopping it silences her at once.
"""
import math
import random
import struct
import wave

RATE = 22050
BASE = 470.0           # Hz: a soft, slightly sleepy middle voice
BLIP = 0.042           # s per blip
VOLUME = 0.16          # at 100%; the default 50% is half of it


def _blip(freq):
    """one blip: a band-limited square (odd harmonics) with a quick pluck envelope"""
    n = int(RATE * BLIP)
    out = []
    for i in range(n):
        t = i / RATE
        s = 0.0
        k = 1
        while k * freq < 4200:
            s += math.sin(2 * math.pi * k * freq * t) / k
            k += 2
        env = min(1.0, t / 0.003) * math.exp(-t / 0.016)
        out.append(s * 0.8 * env)
    return out


_CACHE = {}


def _pitch_blip(step):
    if step not in _CACHE:
        _CACHE[step] = _blip(BASE * 2 ** (step / 12.0))
    return _CACHE[step]


def render(offsets, path, seed=0, end_pitch=0, volume=0.5):
    """write a WAV with a blip at every offset (seconds); the pitch wobbles a little, like
    someone mumbling, and the last few blips follow end_pitch (+1 up for a question)"""
    rnd = random.Random(seed)
    length = (offsets[-1] if offsets else 0) + BLIP + 0.05
    buf = [0.0] * int(RATE * length)
    last = len(offsets) - 1
    for i, t0 in enumerate(offsets):
        step = rnd.choice((0, 0, 0, 1, -1, 2))
        if i >= last - 2 and end_pitch:
            step = 3 * end_pitch
        b = _pitch_blip(step)
        o = int(t0 * RATE)
        for j, v in enumerate(b):
            if o + j < len(buf):
                buf[o + j] += v
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b''.join(struct.pack('<h', int(max(-1.0, min(1.0, v * VOLUME * volume)) * 32767))
                               for v in buf))
    return path

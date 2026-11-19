"""The trained ear «Юми» as a separate little process.

openwakeword lives in its own venv (python 3.11), the ears' daemon in another
one — so the ear works apart: her daemon feeds it the clean microphone sound
over stdin, one frame per command, and it answers with one line of score.

The frames:
  b'R'                        — forget what was heard (the mute ran out)
  b'D' + len(s16) + raw s16   — a frame of sound, one score line back

Run: <oww venv>/bin/python oww_ear.py <model.onnx>
"""

import struct
import sys

import numpy as np
from openwakeword.model import Model


def main():
    model = sys.argv[1] if len(sys.argv) > 1 else 'yumi.onnx'
    m = Model(wakeword_models=[model], inference_framework='onnx')
    stdin, stdout = sys.stdin.buffer, sys.stdout
    while True:
        cmd = stdin.read(1)
        if not cmd:
            break                       # the daemon is gone: the ear goes too
        if cmd == b'R':
            m.reset()
            stdout.write('0.0000\n')    # every frame is answered: the pipe stays aligned
            stdout.flush()
            continue
        (n,) = struct.unpack('<H', stdin.read(2))
        data = stdin.read(n)
        if len(data) < n:
            break
        scores = m.predict(np.frombuffer(data, dtype=np.int16))
        best = max(scores.values()) if scores else 0.0
        stdout.write(f'{best:.4f}\n')
        stdout.flush()


if __name__ == '__main__':
    main()

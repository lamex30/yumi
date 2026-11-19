"""Yumi: a sleepy moth-girl desktop assistant (GTK4 + gtk4-layer-shell).

The shell lives in yumi/ui: the overlay window, its cairo painter, her ears and voice,
the browser-panel bridge and the application itself.  All the behaviour lives in
yumi/brain.py; the shell only draws its ops and wires GTK, the file monitors, the job
queue and the system watchers to it.  This file is the launcher.

  yumi                 start (or do nothing if she is already running)
  yumi --toggle        pop out / hide / wake up (bind it to a key, e.g. Super+Z)
  yumi --pipette       pick a color from the screen
  yumi --home          send her back to the bottom-right corner
  yumi --recent        open the pile of recent files
  yumi --sleep         Do Not Disturb on / off
  yumi --monitor       move her to the next screen
  yumi --ask "фраза"   say it to her as if the mic had heard it (voice, bubble, answer)
  yumi --quit          let her go
Env: YUMI_SCALE (integer pixel scale, overrides settings).
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from yumi.ui.main import main  # noqa: E402


if __name__ == '__main__':
    random.seed()
    sys.exit(main(sys.argv))

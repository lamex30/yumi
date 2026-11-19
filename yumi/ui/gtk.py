"""GTK bootstrap for Yumi's shell.

gtk4-layer-shell is already loaded by bin/yumi; the tools she starts must not inherit it.
The version requirements must run before any other module imports Gtk, Gdk or cairo.
"""
import os

os.environ['LD_PRELOAD'] = ':'.join(p for p in os.environ.get('LD_PRELOAD', '').split(':')
                                    if 'gtk4-layer-shell' not in p)

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Gtk4LayerShell', '1.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_foreign('cairo')


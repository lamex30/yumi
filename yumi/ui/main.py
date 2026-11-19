"""Command line entry point for the overlay: flags become D-Bus actions."""
from gi.repository import GLib

from .app import App
from .constants import ACTIONS, ASK


def main(argv):
    app = App()
    action, param = None, None
    args = argv[1:]
    for i, a in enumerate(args):
        if a == '--' + ASK:
            phrase = args[i + 1] if i + 1 < len(args) else ''
            action, param = ASK, GLib.Variant('s', phrase)
            break
        if a.startswith('--') and a[2:] in ACTIONS:
            action = a[2:]
            break
    try:
        app.register(None)
    except GLib.Error:
        pass
    if app.get_is_remote():
        if action:
            app.activate_action(action, param)
        else:
            app.activate()
        return 0
    if action:
        GLib.idle_add(lambda: app.activate_action(action, param) and False)
    rc = app.run([argv[0]])
    return 1 if app.failed else rc

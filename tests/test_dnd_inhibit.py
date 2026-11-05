#!/usr/bin/env python3
"""Do Not Disturb through org.freedesktop.Notifications inhibitions (KDE Plasma), against a fake daemon.
Run: dbus-run-session -- python3 tests/test_dnd_inhibit.py"""
import os
import subprocess
import sys
import time
from pathlib import Path

from gi.repository import Gio, GLib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
XML = '''<node><interface name="org.freedesktop.Notifications">
<method name="GetCapabilities"><arg type="as" direction="out"/></method>
<method name="Inhibit"><arg type="s"/><arg type="s"/><arg type="a{sv}"/><arg type="u" direction="out"/></method>
<method name="UnInhibit"><arg type="u"/></method>
<property name="Inhibited" type="b" access="read"/>
</interface></node>'''


def fake_daemon():
    active, next_cookie = set(), [0]

    def method(_bus, _sender, _path, _iface, name, args, call):
        if name == 'GetCapabilities':
            call.return_value(GLib.Variant('(as)', (['body', 'inhibitions'],)))
        elif name == 'Inhibit':
            next_cookie[0] += 1
            active.add(next_cookie[0])
            call.return_value(GLib.Variant('(u)', (next_cookie[0],)))
        else:
            active.discard(args[0])
            call.return_value(None)

    def acquired(bus, _name):
        info = Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0]
        bus.register_object('/org/freedesktop/Notifications', info, method,
                            lambda *_a: GLib.Variant('b', bool(active)), None)

    Gio.bus_own_name(Gio.BusType.SESSION, 'org.freedesktop.Notifications', 0, acquired, None, None)
    GLib.MainLoop().run()


def wait(check):
    end = time.time() + 20          # she looks for a daemon every 15 s
    while time.time() < end:
        if check():
            return True
        time.sleep(0.1)
    return False


def main():
    os.environ['PATH'] = ''            # no real swaync-client, it could grab the daemon's name
    from yumi.system import Notifier
    seen = []
    n = Notifier(seen.append)
    assert n.which() != 'inhibit', 'no daemon yet, and none may be auto-started'
    daemon = subprocess.Popen([sys.executable, __file__, 'daemon'])
    try:
        assert wait(lambda: n.which() == 'inhibit')
        inhibited = lambda: n.call('org.freedesktop.DBus.Properties', 'Get',
                                   GLib.Variant('(ss)', ('org.freedesktop.Notifications', 'Inhibited')), '(v)')[0]
        n.set(True)
        assert inhibited() and n.cookie
        assert wait(lambda: seen and seen[-1] is True), seen
        n.set(True)                    # twice must not leak an inhibition: one set(False) still ends it
        n.set(False)
        assert not inhibited() and not n.cookie
        assert wait(lambda: seen[-1] is False), seen
        n.set(False)                   # nothing held: stays off, no error
        assert not inhibited()
    finally:
        daemon.terminate()
    print('ok')


if __name__ == '__main__':
    fake_daemon() if sys.argv[1:] == ['daemon'] else main()

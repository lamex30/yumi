"""X11 in place of wlr-layer-shell: her windows are override-redirect (no window manager
touches them: no frame, no taskbar entry, no tiling), placed over a screen by hand and raised
back on top now and then, except over a fullscreen window. The input shape is GTK's own
set_input_region, which works on X11 as it is. Plain ctypes on libX11, which every X11 session has."""
import ctypes
import ctypes.util

import gi
gi.require_version('GdkX11', '4.0')
from gi.repository import GdkX11  # noqa: E402

_x = ctypes.CDLL(ctypes.util.find_library('X11') or 'libX11.so.6')
_x.XOpenDisplay.restype = ctypes.c_void_p
_x.XOpenDisplay.argtypes = [ctypes.c_char_p]
_x.XDefaultRootWindow.restype = ctypes.c_ulong
_x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
_x.XInternAtom.restype = ctypes.c_ulong
_x.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
for _f, _a in (('XMoveResizeWindow', [ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]),
               ('XRaiseWindow', [ctypes.c_ulong]),
               ('XSetInputFocus', [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]),
               ('XGetInputFocus', [ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int)]),
               ('XFlush', []), ('XFree', None)):
    getattr(_x, _f).argtypes = None if _a is None else [ctypes.c_void_p] + _a


class _Attrs(ctypes.Structure):          # XSetWindowAttributes
    _fields_ = [('background_pixmap', ctypes.c_ulong), ('background_pixel', ctypes.c_ulong),
                ('border_pixmap', ctypes.c_ulong), ('border_pixel', ctypes.c_ulong),
                ('bit_gravity', ctypes.c_int), ('win_gravity', ctypes.c_int),
                ('backing_store', ctypes.c_int), ('backing_planes', ctypes.c_ulong),
                ('backing_pixel', ctypes.c_ulong), ('save_under', ctypes.c_int),
                ('event_mask', ctypes.c_long), ('do_not_propagate_mask', ctypes.c_long),
                ('override_redirect', ctypes.c_int), ('colormap', ctypes.c_ulong),
                ('cursor', ctypes.c_ulong)]


_x.XChangeWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.POINTER(_Attrs)]
_x.XGetWindowProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long, ctypes.c_long,
                                  ctypes.c_int, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
                                  ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong),
                                  ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_void_p)]

CW_OVERRIDE_REDIRECT = 1 << 9
REVERT_TO_PARENT = 2
_dpy = None


def is_x11(display):
    return isinstance(display, GdkX11.X11Display)


_Handler = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
_x.XSetErrorHandler.restype = ctypes.c_void_p
_x.XSetErrorHandler.argtypes = [ctypes.c_void_p]
_gtk_handler = None


@_Handler
def _on_error(dpy, ev):
    """errors on our own connection (focus handed back to a window that has closed) are ignored;
    the rest go on to GTK's handler (the default one would exit the program)"""
    if dpy == _dpy or not _gtk_handler:
        return 0
    return _Handler(_gtk_handler)(dpy, ev)


def _d():
    global _dpy, _gtk_handler
    if _dpy is None:
        _dpy = _x.XOpenDisplay(None)
        _gtk_handler = _x.XSetErrorHandler(ctypes.cast(_on_error, ctypes.c_void_p))
    return _dpy


def _atom(name):
    return _x.XInternAtom(_d(), name.encode(), 0)


def _ulongs(win, prop):
    """an XA_WINDOW / XA_ATOM list property (both come back as longs)"""
    typ, fmt, n, rest, data = ctypes.c_ulong(), ctypes.c_int(), ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p()
    if _x.XGetWindowProperty(_d(), win, _atom(prop), 0, 64, 0, 0, ctypes.byref(typ), ctypes.byref(fmt),
                             ctypes.byref(n), ctypes.byref(rest), ctypes.byref(data)) != 0 or not data.value:
        return []
    out = list(ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))[:n.value]) if fmt.value == 32 else []
    _x.XFree(data)
    return out


def xid(win):
    s = win.get_surface()
    return GdkX11.X11Surface.get_xid(s) if s is not None else 0


def unmanage(win):
    """call between realize and the first map: the window manager will leave it alone"""
    a = _Attrs(override_redirect=1)
    _x.XChangeWindowAttributes(_d(), xid(win), CW_OVERRIDE_REDIRECT, ctypes.byref(a))
    _x.XFlush(_d())


def place(win, x, y, w, h):
    _x.XMoveResizeWindow(_d(), xid(win), x, y, w, h)
    _x.XFlush(_d())


def fullscreen_active():
    act = _ulongs(_x.XDefaultRootWindow(_d()), '_NET_ACTIVE_WINDOW')
    return bool(act and act[0] and _atom('_NET_WM_STATE_FULLSCREEN') in _ulongs(act[0], '_NET_WM_STATE'))


def raise_(win):
    _x.XRaiseWindow(_d(), xid(win))
    _x.XFlush(_d())


_given = {}                                # our window -> the window that had the keyboard before


def focus(win, on):
    """take the keyboard for her (on) or hand it back to where it was"""
    w = xid(win)
    if on and w not in _given:
        prev, rev = ctypes.c_ulong(), ctypes.c_int()
        _x.XGetInputFocus(_d(), ctypes.byref(prev), ctypes.byref(rev))
        _given[w] = prev.value
        _x.XSetInputFocus(_d(), w, REVERT_TO_PARENT, 0)
    elif not on and w in _given:
        prev = _given.pop(w)
        if prev > 1:                       # 0 None, 1 PointerRoot
            _x.XSetInputFocus(_d(), prev, REVERT_TO_PARENT, 0)
    _x.XFlush(_d())

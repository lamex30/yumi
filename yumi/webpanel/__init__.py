"""Yumi's browser panel: a small, local HTTP server with a pixel-art single page app.

The panel is off unless ``web_panel`` is switched on in Yumi's own settings.  It binds
to 127.0.0.1 by default, asks for a random token once, and then keeps a signed,
HttpOnly session with a double-submit CSRF cookie.  Nothing here imports GTK: the live
application hands over a *bridge* object, so the same server can be tested on its own.

    from yumi.webpanel import Panel
    panel = Panel(settings, bridge)
    panel.start()
"""
from .server import Panel, EventBus  # noqa: F401

__all__ = ['Panel', 'EventBus']

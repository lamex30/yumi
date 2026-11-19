"""Names and constants shared by Yumi's GTK shell."""

APP_ID = 'io.github.lamex30.Yumi'
ACTIONS = ('toggle', 'pipette', 'home', 'recent', 'sleep', 'monitor', 'web', 'quit')
ASK = 'ask'          # the one action that carries a phrase («yumi --ask "какая погода"»)
FADE = 0.6          # s of crossfade when the wallpaper palette changes

# GTK paints every window with the theme (USER priority); ours goes one above it.
# Not fully transparent (alpha 0.004 is invisible): GTK gives a fully transparent window
# no pointer input.
CSS = """\
window.moth-girl, window.moth-girl.background, window.moth-girl.csd {
    background: rgba(0, 0, 0, 0.004);
    background-image: none;
    box-shadow: none;
    border: none;
    border-radius: 0;
    outline: none;
}
window.moth-girl drawingarea { background: none; }
"""

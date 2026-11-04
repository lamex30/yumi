#!/bin/sh
# Install Yumi.
#   ./install.sh                         just for you, into ~/.local
#   sudo PREFIX=/usr/local ./install.sh  for everyone
#   ./install.sh --uninstall             (same PREFIX as when installing)
# Packagers: PREFIX=/usr DESTDIR="$pkgdir" ./install.sh
set -e
cd "$(dirname "$0")"
PREFIX="${PREFIX:-$HOME/.local}"
ROOT="${DESTDIR:-}$PREFIX"
APPDIR="$ROOT/share/yumi"
ID=io.github.lamex30.Yumi

if [ "$1" = --uninstall ]; then
    rm -rf "$APPDIR"
    rm -f "$ROOT/bin/yumi" "$ROOT/share/applications/$ID.desktop" \
          "$ROOT/share/icons/hicolor/256x256/apps/$ID.png"
    echo "Yumi removed from $PREFIX"
    exit 0
fi

mkdir -p "$APPDIR" "$ROOT/bin" "$ROOT/share/applications" "$ROOT/share/icons/hicolor/256x256/apps"
rm -rf "$APPDIR/yumi" "$APPDIR/sprites"
tar --exclude=__pycache__ --exclude='*.pyc' -cf - moth_girl.py yumi sprites sounds actions.example.json \
    | tar -xf - -C "$APPDIR"
sed "s|^DIR=.*# @DIR@.*|DIR=\"$PREFIX/share/yumi\"|" bin/yumi > "$ROOT/bin/yumi"
chmod 755 "$ROOT/bin/yumi"
cp data/$ID.desktop "$ROOT/share/applications/$ID.desktop"
cp data/$ID.png "$ROOT/share/icons/hicolor/256x256/apps/$ID.png"
[ -z "$DESTDIR" ] && command -v gtk-update-icon-cache >/dev/null 2>&1 && \
    gtk-update-icon-cache -q -t "$ROOT/share/icons/hicolor" 2>/dev/null || true

echo "Yumi installed into $PREFIX. Start her with: yumi"
case ":$PATH:" in
    *":$PREFIX/bin:"*) ;;
    *) [ -z "$DESTDIR" ] && echo "(add $PREFIX/bin to your PATH first)" ;;
esac

{ lib, stdenv, python3, gtk4, gtk4-layer-shell, gobject-introspection, wrapGAppsHook4
, glib, playerctl, cava, swayidle, wl-clipboard, grim, slurp }:

let
  python = python3.withPackages (p: [ p.pygobject3 p.pycairo ]);
in
stdenv.mkDerivation {
  pname = "yumi-moth";
  version = "1.2.0";
  src = ../..;

  nativeBuildInputs = [ wrapGAppsHook4 gobject-introspection ];
  buildInputs = [ gtk4 gtk4-layer-shell python ];

  dontBuild = true;
  installPhase = ''
    PREFIX=$out sh install.sh
  '';

  # gtk4-layer-shell must be preloaded (bin/yumi does it from this path); the extras are optional
  preFixup = ''
    gappsWrapperArgs+=(
      --set YUMI_LAYER_SHELL_LIB ${gtk4-layer-shell}/lib/libgtk4-layer-shell.so
      --prefix PATH : ${lib.makeBinPath [ python glib playerctl cava swayidle wl-clipboard grim slurp ]}
    )
  '';

  meta = {
    description = "A sleepy pixel-art moth girl who lives at the bottom of your Wayland screen";
    homepage = "https://github.com/lamex30/yumi";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
    mainProgram = "yumi";
  };
}

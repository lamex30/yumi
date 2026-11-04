{
  description = "Yumi, a sleepy pixel-art moth girl for Wayland desktops";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      forAll = f: nixpkgs.lib.genAttrs [ "x86_64-linux" "aarch64-linux" ]
        (system: f nixpkgs.legacyPackages.${system});
    in {
      packages = forAll (pkgs: rec {
        yumi = pkgs.callPackage ./packaging/nix/package.nix { };
        default = yumi;
      });
      overlays.default = final: _: { yumi-moth = final.callPackage ./packaging/nix/package.nix { }; };
    };
}

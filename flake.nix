{
  description = "Suspends processes with SIGSTOP/SIGCONT to protect a host from thrashing";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
      # A store path has no tags for hatch-vcs to read.  No "dev" in the
      # version: thrash-protect would auto-enable diagnostic logging for it.
      version = "0+git${self.lastModifiedDate}.g${self.shortRev or self.dirtyShortRev or "unknown"}";
    in
    {
      packages = forAllSystems (pkgs: rec {
        thrash-protect = pkgs.callPackage ./nix/package.nix {
          src = self;
          inherit version;
        };
        default = thrash-protect;
      });

      overlays.default = final: prev: {
        thrash-protect = final.callPackage ./nix/package.nix {
          src = self;
          inherit version;
        };
      };

      nixosModules.default =
        { pkgs, lib, ... }:
        {
          imports = [ ./nix/module.nix ];
          services.thrash-protect.package =
            lib.mkDefault
              self.packages.${pkgs.stdenv.hostPlatform.system}.thrash-protect;
        };

      checks = forAllSystems (pkgs: {
        package = self.packages.${pkgs.stdenv.hostPlatform.system}.thrash-protect;
        nixos = pkgs.testers.runNixOSTest (
          import ./nix/test.nix {
            module = self.nixosModules.default;
            package = self.packages.${pkgs.stdenv.hostPlatform.system}.thrash-protect;
          }
        );
      });

      formatter = forAllSystems (pkgs: pkgs.nixfmt);
    };
}

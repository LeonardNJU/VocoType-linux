{
  description = "VoCoType Linux native voice input for Fcitx 5 and IBus";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/b6018f87da91d19d0ab4cf979885689b469cdd41";

  outputs = { self, nixpkgs }:
    let
      supportedSystems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
      packagesFor = system:
        let
          pkgs = import nixpkgs { inherit system; };
          callPackage = flavor: pkgs.callPackage ./packaging/nix/package.nix {
            inherit flavor;
            source = self;
          };
          universal = callPackage "universal";
          ibus = callPackage "ibus";
          fcitx5 = callPackage "fcitx5";
        in {
          vocotype = universal;
          vocotype-universal = universal;
          vocotype-ibus = ibus;
          vocotype-fcitx5 = fcitx5;
          vocotype-funasr-source = universal.funasrSource;
          vocotype-funasr-workers = universal.workers;
          default = universal;
        };
    in {
      packages = forAllSystems packagesFor;
      apps = forAllSystems (system:
        let packages = packagesFor system;
        in {
          default = {
            type = "app";
            program = "${packages.default}/bin/vocotype-settings";
          };
          settings = {
            type = "app";
            program = "${packages.default}/bin/vocotype-settings";
          };
        });
      formatter = forAllSystems (system: nixpkgs.legacyPackages.${system}.nixfmt-rfc-style);
    };
}

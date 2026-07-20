# Packaging and distribution

VoCoType has three supported distribution layers:

1. **Python wheel and sdist** for development, library use, and runtime tooling.
2. **Complete source bundle** (`VocoType-linux-<version>.tar.gz`) containing the graphical installers, Fcitx module source, IBus engine, documentation, and locked dependencies.
3. **Native Linux packages**: DEB for Debian/Ubuntu, RPM for Fedora/RHEL-family, and PKGBUILD packages for Arch Linux. These install desktop integration and the compiled Fcitx global module system-wide.

Native packages deliberately do not run `pip`, download models, write user configuration, or start a user service during the package-manager transaction. After installation, open **VoCoType Settings**. The graphical installer creates an isolated Python 3.12 environment in the user's home directory and performs any optional model download or framework repair with normal Polkit authorization.

The settings center can remove either user-level integration without bypassing package ownership. Files under `/usr` remain managed by `apt`, `dnf`, or `pacman`; the GUI reports the matching `vocotype-linux` removal command when a native package is detected.

## Local commands

```bash
make test
make release                 # source archive + wheel + sdist + checksums
make package-deb             # Debian/Ubuntu host or container
make package-rpm             # Fedora/RPM host or container
make package-arch            # Arch host or container
```

Artifacts are written below `dist/release/` and `dist/packages/`.

## Release tags

Pushing a tag matching `v<version>` runs the release workflow. The tag version must equal `vocotype_version.__version__`; the build fails on a mismatch. GitHub Release assets include the source archive, Python distributions, native packages produced by the available builders, a JSON manifest, and SHA-256 checksums.

## Package contract

A native package must install:

- `/usr/share/vocotype/`: complete setup/runtime source tree and a `.system-package` marker;
- the Fcitx global module and addon metadata;
- the IBus component and launcher (`/usr/libexec` on DEB/RPM, `/usr/lib/vocotype` on Arch);
- `vocotype-settings`, backend, and recorder launchers;
- a systemd user service definition, desktop entry, icon, license, and documentation.

Package maintainer scripts are non-interactive and never download network content.

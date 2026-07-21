# Packaging and distribution

VoCoType has three supported distribution layers:

1. **Python wheel and sdist** for development, library use, and runtime tooling.
2. **Complete source bundle** (`VocoType-linux-<version>.tar.gz`) containing the graphical installers, Fcitx module source, IBus engine, documentation, and locked dependencies.
3. **Complete native Linux packages**: DEB for Debian/Ubuntu, RPM for Fedora/RHEL-family, and PKGBUILD packages for Arch Linux. Each distro publishes three flavors from one staging implementation: `vocotype-linux` (universal), `vocotype-linux-ibus`, and `vocotype-linux-fcitx5`. Every flavor includes the audited native 2-pass runtime and locked Python 3.12 runtime closure; specialized flavors omit the other input-framework integration and dependency.

Native package-manager transactions do not run `pip`, download models, write user configuration, or start a user service. After installation, open **VoCoType Settings**. The graphical installer creates an isolated Python 3.12 environment from the package-local wheelhouse with `--no-index --only-binary`, then downloads only the selected models. AI features call an OpenAI-compatible API supplied by the user. VoCoType never installs, starts, warms, or stops an SLM process; source builds remain forbidden on the user machine. No compiler or source build is permitted on the user machine. System-level registration or repair uses the desktop Polkit agent; VoCoType never reads an administrator password.

The settings center can remove either user-level integration without bypassing package ownership. Files under `/usr` remain managed by `apt`, `dnf`, or `pacman`; the GUI reads `.system-package` and reports the exact flavor-specific removal command.

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

Pushing `v3.0.0-rc.1` for internal version `3.0.0rc1` builds and publishes a GitHub **Pre-release**. If defects are found, create `rc.2`; never move an existing RC tag. Pushing the formal `v3.0.0` tag builds a **Draft** Release. Download and smoke-test those exact draft assets, then publish the existing draft without rebuilding.

Every Release build first creates one portable native streaming artifact. The DEB, RPM, and Arch jobs consume that same audited artifact, build a PyGObject-compatible wheelhouse for their own distro, install the resulting package, and verify a fresh Python 3.12 runtime using only package-local wheels. GitHub Release assets include the source archive, Python distributions, complete native packages, the standalone native bundle, machine-readable manifests, and SHA-256 checksums.

## Package contract

A native package must install:

- `/usr/share/vocotype/`: complete setup/runtime source tree, distro-compatible wheelhouse, and a `.system-package` marker;
- the Fcitx global module and addon metadata;
- the IBus component and launcher (`/usr/libexec` on DEB/RPM, `/usr/lib/vocotype` on Arch);
- `vocotype-settings`, backend, recorder, and precompiled native streaming launchers with private runtime libraries;
- a systemd user service definition, desktop entry, icon, license, and documentation.

Package maintainer scripts are non-interactive and never download network content. Missing native or wheelhouse inputs fail the build; incomplete packages are never published.

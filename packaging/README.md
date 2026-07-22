# Packaging and distribution

VoCoType publishes three native Linux package flavors from one staging implementation:
The same contract produces DEB, RPM, and Arch Linux packages.


- `vocotype-linux`: Fcitx 5 + IBus
- `vocotype-linux-fcitx5`: Fcitx 5 only
- `vocotype-linux-ibus`: IBus only

The installed runtime is native-only. Packages contain compiled ELF executables, the Fcitx module or IBus engine, the audited FunASR/ONNX runtime, desktop resources, and configuration/install scripts. They do **not** contain a Python interpreter, virtual environment, wheelhouse, `.py` runtime modules, NumPy, SoundDevice, PyGObject, or Python path launchers.

Python remains usable on the build machine for tests, version rendering, and source-archive tooling. It is not an installed runtime dependency.

## Runtime components

A universal package installs:

- `vocotype-core`
- `vocotype-streaming-worker`
- `vocotype-offline-worker`
- `vocotype-audio-recorder`
- `vocotype-model-manager` — native model manager
- `vocotype-settings`
- `vocotype-ibus-engine`
- the Fcitx 5 global module

The package transaction is offline and non-interactive. ASR models are user data and are validated or downloaded later by `vocotype-model-manager` through the settings center or native installer. Existing models under the ModelScope cache are reused after SHA-256 validation.

## Local commands

```bash
make test
make release
make package-deb
make package-rpm
make package-arch
```

The package jobs consume one audited portable native FunASR/ONNX bundle. Every produced package is extracted before installation and checked for:

- zero `.py`, `.pyc`, and `.whl` runtime files;
- expected ELF executables;
- resolved shared-library dependencies;
- native payload checksums;
- correct flavor-specific IBus/Fcitx files.

Artifacts are written below `dist/release/` and `dist/packages/`.

# Repository layout

```text
native/core/              C++ core daemon: ASR dispatch, ITN, terminology, SLM, edit planning
native/streaming_worker/  C++ FunASR offline/streaming workers and audited bundle build
native/desktop/           C++ GTK settings, PortAudio recorder/playback, IBus engine, librime
fcitx5/module/            C++ Fcitx 5 global module
fcitx5/common/            C++ Unix-socket IPC client
ibus/data/                IBus component metadata
installers/               Native user install/uninstall lifecycle scripts
packaging/                DEB/RPM/Arch templates, shell builders, audits, and smoke tests
tests/native-contracts.sh Native architecture and product-behavior contracts
tools/test-native.sh      CTest/build/package contract entry point
feedback_service/         Independently deployed feedback receiver; not a desktop dependency
```

The desktop/client tree contains no Python implementation or fallback. Python is
not installed by VoCoType packages. The feedback receiver is a separate server
application and is never copied into desktop packages.

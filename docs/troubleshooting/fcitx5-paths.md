# Fcitx 5 paths and diagnostics

## Native user installation

```text
~/.local/lib/fcitx5/vocotype.so
~/.local/share/fcitx5/addon/vocotype.conf
~/.local/lib/vocotype-streaming/bin/vocotype-core
~/.local/lib/vocotype-streaming/bin/vocotype-offline-worker
~/.local/lib/vocotype-streaming/bin/vocotype-streaming-worker
~/.local/lib/vocotype-native/bin/vocotype-audio-recorder
~/.local/lib/vocotype-native/bin/vocotype-settings
~/.config/systemd/user/vocotype-fcitx5-backend.service
```

## Configuration

```text
~/.config/vocotype/fcitx5-backend.json
~/.config/vocotype/audio.conf
~/.config/vocotype/terms.yaml
~/.config/fcitx5/conf/vocotype.conf
```

## Checks

```bash
tools/diagnostics/native-doctor.sh
journalctl --user -u vocotype-fcitx5-backend.service -n 100
```

The Fcitx daemon must be started by the graphical desktop session. The installer
restarts KDE's desktop autostart unit when available; it does not launch a
display-less Fcitx process merely because a D-Bus address exists.

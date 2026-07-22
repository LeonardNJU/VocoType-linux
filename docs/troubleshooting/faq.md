# FAQ

## F9 does nothing

Run:

```bash
tools/diagnostics/native-doctor.sh
```

Confirm `/tmp/vocotype-fcitx5.sock` is owned by `vocotype-core`, and that no
VoCoType Python process exists.

## Fcitx has no candidate window

Check the Fcitx process environment:

```bash
pid=$(pgrep -x fcitx5 | head -1)
tr '\0' '\n' < /proc/$pid/environ | grep -E 'DISPLAY|WAYLAND_DISPLAY'
```

If neither variable exists, restart Fcitx through the desktop autostart unit,
not from SSH or a headless service.

## Ctrl+F9 deletes text but does not insert the replacement

Current native versions validate the surrounding snapshot first, then issue
`deleteSurroundingText` and `commitString` in the same input-method transaction.
They do not use a delayed surrounding-text cache as deletion acknowledgement.
Reinstall the latest Fcitx module if this old failure still occurs.

## How do I choose a microphone or speaker?

Open **VoCoType Settings → Recognition** for the input device and **Playground**
for the playback output. Recording, waveform display, playback, and resampling
all use native PortAudio code.

## How do I verify models?

Use **VoCoType Settings → Overview → Validate and download models**. The native
model manager pins every required file to an immutable ModelScope revision and
checks SHA-256 before accepting it.

## How do I collect diagnostics?

Open **Doctor and Support** in the compiled settings center. It can verify ELF
integrity, create a redacted support archive, open the support directory, and
create a GitHub issue.

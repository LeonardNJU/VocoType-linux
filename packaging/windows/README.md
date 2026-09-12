# VocoType Windows native preview

This is an experimental native Windows x64 build, not the Linux/macOS stable
release. Windows 11 or Windows 10 version 1903+ is required. Do not run it as an
administrator. Microphone access for **desktop apps** must be enabled in Windows
Settings. No Windows service or system-wide keyboard layout is installed.

## Set up

Extract the complete portable ZIP to a writable folder. Keep every EXE and DLL
together. In PowerShell 7, run `./setup-models.ps1` from that folder. This explicitly
downloads the same pinned, SHA-256-verified local models used by the existing
native model manager; models are not inside the ZIP. The script refuses to
replace an existing configuration unless `-Force` is supplied. Default storage:

- Models: `%LOCALAPPDATA%\VocoType\models`
- Configuration: `%LOCALAPPDATA%\VocoType\windows.json`

Launch `vocotype-windows.exe`. It loads the model without opening the microphone.
When ready, hold **F9**, speak, and release F9. **Escape** cancels the current
operation. **Shift+F9** uses the existing optional polishing path; polishing is
disabled by default and requires configuring an SLM endpoint deliberately.
No audio is sent to a network ASR service. Model setup uses network downloads.

Right-click the tray icon to view the full previous result/error, reload the
backend, or exit. Closing the result window hides it without stopping the tray
app. The microphone is used only during a recording. The floating preview retains
the latest 40 Unicode code points; the full audio and final text are not truncated.
Preview truncation is surrogate-safe, but does not keep every combining/ZWJ
sequence together.

Text is sent only when the original foreground and native focused windows still
match and no modifier key is held. Higher-integrity/elevated apps can reject
SendInput. The complete result remains in the result window for manual copying.
Do not blindly retry insertion: Windows can partially accept an input batch.
The preview never rewrites the clipboard automatically. Browser-internal DOM
focus changes cannot be fully validated from a native window handle; test each
application before relying on automatic insertion for important work.

## Diagnostic commands

```powershell
./vocotype-windows.exe --audio-probe
./vocotype-windows.exe --config C:\path\windows.json --record-ms 3000
./vocotype-windows.exe --config C:\path\windows.json --transcribe C:\path\sample.wav
```

The last two commands perform real local recognition and print JSON. CLI recording
uses final recognition only; the tray UI also streams previews. The helper accepts
`--device-id` with a WASAPI endpoint ID; set `windows.device_id` in the configuration
to use a non-default microphone.

## Implementation and limits

The Windows frontend uses the same C++ core dispatcher, asynchronous transcription,
text normalization, and optional SLM logic as the existing app. Its Windows
transport uses private, local-only named pipes, CreateProcessW, a strict inherited
handle list, and job objects that contain the worker tree. The recorder is a
separate WASAPI process. Missing first audio, later stalls, and stop requests have
bounded waits; a broken recorder does not block the UI thread indefinitely.
Preview and final inference use separate core processes so preview transport
failure does not destroy final recording data. Recordings are limited to 15 minutes.

Unit/integration tests explicitly use test-only audio/decoder fixtures and must
not be confused with real model inference or physical microphone tests. Native
FunASR worker builds and recorded-file inference are tested separately in the
Windows workflow. There is no Windows physical device attached to the development
session; hardware, sleep/wake, Bluetooth, and individual target-app compatibility
still require hands-on testing. This is not a TSF IME or a signed production installer.

## Build from source

Requires Visual Studio C++ tools, CMake, Git, PowerShell 7, and vcpkg.
Set `VCPKG_INSTALLATION_ROOT`, then from the repository root:

```powershell
./packaging/windows/configure.ps1
cmake --build build/windows --config Release --parallel
ctest --test-dir build/windows -C Release --output-on-failure
./packaging/windows/build-funasr.ps1
./packaging/windows/package-preview.ps1 -NativeBundle build/windows-runtime/bundle
```

The native worker script pins FunASR and the ONNX Runtime SDK, verifies the SDK
hash, and patches only a disposable source copy. The stable release tag is not
changed by building this preview.

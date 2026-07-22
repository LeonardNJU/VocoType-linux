# VoCoType native C++ core

`vocotype-core` is the default speech backend for packaged Fcitx 5 and IBus
installations. It preserves the existing Unix-socket JSON protocol, so the
Fcitx module and the IBus Python/GObject shell share one implementation of ASR,
normalization, polishing, and voice editing.

## Implemented

- Final Contextual Paraformer ASR through the official FunASR C++ runtime.
- Dynamic contextual hotwords, optional VAD, punctuation, and automatic audio
  resampling in a disposable offline worker process.
- Native Chinese ITN with the existing context guards and 950 protected fixed
  phrases.
- Live terminology YAML reload, longest-match canonicalization, protected
  spans, legacy dictionary compatibility, and native hotword generation.
- Two-pass online preview through the official native streaming worker.
- F9 synchronous transcription.
- Shift+F9 asynchronous start/poll/cancel with OpenAI-compatible SSE deltas,
  thinking suppression, heartbeat-aware idle timeout, and JSON fallback.
- Synchronous and asynchronous voice editing with strict JSON-plan validation,
  key and modifier allowlists, repeat clamps, and a 32-action safety limit.
- Bounded task retention, recording cleanup, worker idle exit, signal handling,
  typed configuration, fake-worker tests, socket tests, and real-model smoke
  tests.

## Framework integration

- Fcitx 5 launches `vocotype-core` directly when it is installed.
- IBus retains its GObject/input-method shell and Rime adapter in Python, but
  delegates preview, final ASR, ITN, terminology, polishing, and edit planning
  to a dedicated native-core socket.
- `VOCOTYPE_BACKEND=python` forces the legacy Python inference path for
  rollback. `VOCOTYPE_BACKEND=cpp` requires the native core and fails clearly
  when it is absent. The default `auto` mode prefers native and falls back only
  when no native binary is installed.

## Build and test

```bash
make cpp-core-test
```

The FunASR worker bundle is built separately:

```bash
./native/streaming_worker/build.sh
```

Run an isolated development socket:

```bash
build/native-core/vocotype-core \
  --enable-final-asr \
  --socket-path /tmp/vocotype-cpp-test.sock
```

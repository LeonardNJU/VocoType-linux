# VoCoType native C++ core

`vocotype-core` is an opt-in C++20 replacement for the Python Fcitx backend.
It preserves the existing Unix-socket JSON protocol so the current Fcitx
module and recorder can use either backend without recompilation.

## Implemented

- `/tmp/vocotype-fcitx5.sock` request/response server with mode `0600`.
- Final Contextual Paraformer ASR through the official FunASR C++ runtime.
- Dynamic contextual hotwords, optional VAD, punctuation, and automatic audio
  resampling in a disposable offline worker process.
- Two-pass online preview through the existing native streaming worker.
- Plain F9 synchronous transcription.
- Shift+F9 asynchronous `transcribe_start` / `polish_poll` / cancel protocol.
- OpenAI-compatible polishing through libcurl.
- Synchronous and asynchronous voice editing with strict JSON-plan validation,
  key and modifier allowlists, repeat clamps, and a 32-action safety limit.
- Owned recording cleanup, worker idle exit, signal handling, typed config
  loading, unit tests, fake-worker tests, Unix-socket tests, and real-model
  smoke tests.

## Deliberately not the default yet

The native core is packaged for A/B testing but the normal launcher still uses
the Python backend. Text normalization and terminology canonicalization have
not yet been ported, and the native SLM path currently returns final responses
rather than incremental SSE deltas. IBus also still uses its Python engine.

## Build and test

```bash
make cpp-core-test
```

The ASR worker bundle is built separately:

```bash
./native/streaming_worker/build.sh
```

Run an isolated development socket:

```bash
build/native-core/vocotype-core \
  --enable-final-asr \
  --socket-path /tmp/vocotype-cpp-test.sock
```

A packaged Fcitx installation can opt in through
`VOCOTYPE_BACKEND=cpp`; see `docs/guides/native-core.md`.

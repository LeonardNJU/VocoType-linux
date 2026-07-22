# VoCoType native core prototype

This directory is the first isolated step toward removing the user-visible
Python runtime from VoCoType.

## Implemented

- C++20 Unix-domain JSON server compatible with the current Fcitx request
  framing: one connection, one JSON request, response read until EOF.
- Existing `/tmp/vocotype-fcitx5.sock` default and mode `0600`.
- Recursive loading of the existing JSON configuration shape.
- `ping`, `capabilities`, and `edit_applied` requests.
- Native non-streaming OpenAI-compatible SLM polishing through libcurl.
- Explicit ASR boundary: final ASR requests currently return
  `native_final_asr_not_connected` rather than silently invoking Python.
- Unit and Unix-socket integration tests.

## Build

```bash
cmake -S native/core -B build/native-core -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build build/native-core -j
ctest --test-dir build/native-core --output-on-failure
```

Run it on a separate socket while the Python backend is still installed:

```bash
build/native-core/vocotype-core \
  --socket-path /tmp/vocotype-cpp-test.sock
```

The next implementation should introduce an `AsrEngine` interface and bind it
to the pinned official FunASR C++ runtime. The offline Contextual Paraformer
path must be compared against the existing Python backend on a golden WAV
corpus before this daemon replaces the production socket.

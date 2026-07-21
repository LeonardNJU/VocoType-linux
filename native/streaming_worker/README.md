# Native streaming ASR worker

`vocotype-streaming-worker` is a thin JSON-lines process wrapper around the
**official FunASR C++ ONNX runtime**. It does not reimplement Paraformer's
online frontend, CIF, overlap or FSMN cache logic.

The build is pinned to FunASR commit
`bd6e72142f1cca3c30b7651bf5fa567dfe969810`. The build pins the official
CPU-only ONNX Runtime 1.23.2 SDK, verifies its release checksum, and downloads
it automatically when `ONNXRUNTIME_DIR` is not supplied:

```bash
./native/streaming_worker/build.sh
```

For a fully offline build, set `ONNXRUNTIME_DIR` to an already extracted SDK.

The v3 release workflow builds this runtime once in a controlled Ubuntu 22.04 job, audits it, and injects the same bundle into the DEB, RPM, and Arch packages. End users never build it locally; only source developers invoke this script directly.

For local source inspection or offline builds, set `FUNASR_SOURCE_DIR` to an
existing FunASR checkout. The resulting relocatable local bundle is written to
`native/streaming_worker/build/bundle/` (`bin/` plus its private `lib/`).

The process owns the optional online model and exits after its configured idle
timeout. IBus and Fcitx therefore never load online ONNX sessions into their
own long-lived processes. Final committed text remains the output of
VoCoType's existing Contextual Paraformer path.

## Toolchain compatibility

The pinned upstream runtime vendors older Kaldi/OpenFST snapshots. The build
applies `funasr-toolchain-compat.patch` only to its disposable build copy to add
a missing `<cstdint>` include and fix two ownership/copy typos rejected by
modern compilers. No Paraformer inference or cache logic is changed.

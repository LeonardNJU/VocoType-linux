# Native FunASR workers

This directory builds two thin JSON-lines process wrappers around the
**official FunASR C++ ONNX runtime**:

- `vocotype-streaming-worker` owns the online Paraformer state used for
  two-pass live preview.
- `vocotype-offline-worker` owns final Contextual Paraformer inference,
  contextual hotword embeddings, optional VAD, punctuation, and resampling.

VoCoType does not reimplement Paraformer's frontend, CIF, decoder, overlap,
FSMN cache, VAD, or punctuation algorithms. Both workers call the pinned
upstream runtime directly.

The build is pinned to FunASR commit
`bd6e72142f1cca3c30b7651bf5fa567dfe969810` and CPU-only ONNX Runtime
1.23.2. The SDK checksum is verified before use.

```bash
./native/streaming_worker/build.sh
```

For offline development builds, set `FUNASR_SOURCE_DIR` and `ONNXRUNTIME_DIR`
to existing source and SDK directories. The relocatable result is written to:

```text
native/streaming_worker/build/bundle/
├── bin/
│   ├── vocotype-streaming-worker
│   └── vocotype-offline-worker
└── lib/
```

Release CI builds the workers once on Ubuntu 22.04, audits every ELF dependency
and RUNPATH, and injects the same bundle into DEB, RPM, and Arch packages.
End users never compile FunASR locally.

Both workers exit after configurable idle periods. Model memory is therefore
reclaimed by normal process exit without destabilizing the long-lived input
method process.

## Toolchain compatibility

The pinned upstream runtime vendors older Kaldi/OpenFST and yaml-cpp snapshots.
`funasr-toolchain-compat.patch` is applied only to a disposable build copy to
add a missing `<cstdint>` include and fix ownership/copy errors rejected by
modern compilers. Inference behavior is not changed.

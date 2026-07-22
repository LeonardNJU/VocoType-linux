# Third-party notices

VoCoType's native desktop runtime links against or bundles the following projects under their respective licenses:

- FunASR / FunASR ONNX runtime
- ONNX Runtime
- PortAudio
- GTK 3 and GLib
- Fcitx 5
- IBus
- librime and Rime data
- yaml-cpp
- nlohmann/json
- libcurl
- OpenSSL
- Boost.Asio / Boost.Beast
- SQLite
- OpenFST, glog, and dependencies included by the audited FunASR bundle

The portable native bundle copies upstream license and notice files into
`share/licenses/`. Distribution packages install those notices under the
platform's standard license directory.

The repository contains no Python implementation or Python dependency manifest.
Desktop clients, the feedback receiver, package/release tooling, and the static
documentation builder are compiled C++ or shell components.

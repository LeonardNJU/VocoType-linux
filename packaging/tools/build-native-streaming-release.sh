#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
OUT=${1:-"$ROOT/dist/native"}
ARCH=${VOCOTYPE_NATIVE_ARCH:-$(uname -m)}
case "$ARCH" in
  amd64) ARCH=x86_64 ;;
  arm64) ARCH=aarch64 ;;
esac

"$ROOT/native/streaming_worker/build.sh" >/tmp/vocotype-native-worker-path.txt
bundle="$ROOT/native/streaming_worker/build/bundle"
core_build="$ROOT/build/native-core-release"
rm -rf "$core_build"
cmake -S "$ROOT/native/core" -B "$core_build" \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF
cmake --build "$core_build" --target vocotype-core -j"${JOBS:-2}"
install -m755 "$core_build/vocotype-core" "$bundle/bin/vocotype-core"
if command -v strip >/dev/null 2>&1; then
  strip --strip-unneeded "$bundle/bin/vocotype-core" 2>/dev/null || true
fi
"$ROOT/native/streaming_worker/audit_bundle.sh" "$bundle"
for required in \
  bin/vocotype-core \
  bin/vocotype-streaming-worker \
  bin/vocotype-offline-worker \
  lib/libfunasr.so \
  share/licenses/onnxruntime/LICENSE \
  share/licenses/funasr/LICENSE; do
  test -e "$bundle/$required" || { echo "missing native bundle file: $required" >&2; exit 1; }
done

mkdir -p "$OUT"
archive="$OUT/vocotype-native-streaming-linux-$ARCH.tar.gz"
rm -f "$archive" "$archive.sha256"
# Preserve SONAME symlinks while making the artifact reproducible.
tar --sort=name --mtime='UTC 1970-01-01' --owner=0 --group=0 --numeric-owner \
  -C "$bundle" -czf "$archive" .
(
  cd "$OUT"
  sha256sum "$(basename "$archive")" > "$(basename "$archive").sha256"
)
printf '%s\n' "$archive"

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
python3 "$ROOT/native/streaming_worker/audit_bundle.py" "$bundle"
for required in \
  bin/vocotype-streaming-worker \
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

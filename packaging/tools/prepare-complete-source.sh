#!/usr/bin/env bash
set -euo pipefail
SOURCE= BUNDLE= OUTPUT=
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SOURCE=${2:?}; shift 2 ;;
    --native-bundle) BUNDLE=${2:?}; shift 2 ;;
    --flavor) shift 2 ;;
    --output) OUTPUT=${2:?}; shift 2 ;;
    -h|--help) echo "Usage: $0 --source ARCHIVE --native-bundle DIR --output ARCHIVE"; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -f "$SOURCE" && -d "$BUNDLE" && -n "$OUTPUT" ]] || { echo "source, bundle, and output are required" >&2; exit 2; }
for path in bin/vocotype-core bin/vocotype-streaming-worker bin/vocotype-offline-worker lib/libfunasr.so share/licenses/onnxruntime/LICENSE share/licenses/funasr/LICENSE; do
  [[ -e "$BUNDLE/$path" ]] || { echo "Incomplete native bundle: $path" >&2; exit 1; }
done
for executable in vocotype-core vocotype-streaming-worker vocotype-offline-worker; do
  [[ -x "$BUNDLE/bin/$executable" ]] || { echo "Not executable: $executable" >&2; exit 1; }
done
while IFS= read -r entry; do
  [[ "$entry" != /* && "$entry" != ../* && "$entry" != */../* ]] || { echo "Unsafe archive member: $entry" >&2; exit 1; }
done < <(tar -tzf "$SOURCE")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
tar -xzf "$SOURCE" -C "$work" --no-same-owner
mapfile -t roots < <(find "$work" -mindepth 1 -maxdepth 1 -type d)
[[ ${#roots[@]} -eq 1 ]] || { echo "Source archive must have one root" >&2; exit 1; }
root=${roots[0]}
rm -rf "$root/native/streaming_worker/build/bundle"
mkdir -p "$root/native/streaming_worker/build"
cp -a "$BUNDLE" "$root/native/streaming_worker/build/bundle"
mkdir -p "$(dirname "$OUTPUT")"
tar --sort=name --mtime='@0' --owner=0 --group=0 --numeric-owner \
  -C "$work" -czf "$OUTPUT" "$(basename "$root")"
echo "$OUTPUT"

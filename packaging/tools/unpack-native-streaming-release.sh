#!/usr/bin/env bash
set -euo pipefail
archive=${1:?usage: $0 ARCHIVE DESTINATION}
destination=${2:?usage: $0 ARCHIVE DESTINATION}
archive=$(realpath "$archive")
checksum="$archive.sha256"
test -f "$checksum" || { echo "missing native bundle checksum: $checksum" >&2; exit 1; }
(
  cd "$(dirname "$archive")"
  sha256sum -c "$(basename "$checksum")"
)
rm -rf "$destination"
mkdir -p "$destination"
tar -xzf "$archive" -C "$destination"
root=$(cd "$(dirname "$0")/../.." && pwd)
python3 "$root/native/streaming_worker/audit_bundle.py" "$destination"
printf '%s\n' "$destination"

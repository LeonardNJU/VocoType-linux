#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
. "$ROOT/packaging/tools/package-common.sh"
OUTPUT="$ROOT/dist/release"
TREEISH=HEAD
ALLOW_DIRTY=false
KEEP=false
EXPECTED=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output) OUTPUT=${2:?}; shift 2 ;;
    --treeish) TREEISH=${2:?}; shift 2 ;;
    --expected-version) EXPECTED=${2:?}; shift 2 ;;
    --allow-dirty) ALLOW_DIRTY=true; shift ;;
    --keep-output) KEEP=true; shift ;;
    --source-only) shift ;;
    -h|--help) echo "Usage: $0 [--output DIR] [--treeish REF] [--expected-version VERSION] [--allow-dirty]"; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
VERSION=$(vocotype_version "$ROOT")
[[ -z "$EXPECTED" || "$EXPECTED" == "$VERSION" ]] || { echo "Expected $EXPECTED, VERSION is $VERSION" >&2; exit 2; }
if [[ "$ALLOW_DIRTY" != true && "$TREEISH" == HEAD && -n $(git -C "$ROOT" status --porcelain --untracked-files=normal) ]]; then
  echo "Refusing to build from a dirty tree; pass --allow-dirty for a preview" >&2
  exit 2
fi
[[ "$KEEP" == true ]] || rm -rf "$OUTPUT"
mkdir -p "$OUTPUT"
COMMIT=$(git -C "$ROOT" rev-parse "$TREEISH")
ARCHIVE="$OUTPUT/VocoType-linux-$VERSION.tar.gz"
git -C "$ROOT" archive --format=tar --prefix="VocoType-linux-$VERSION/" "$TREEISH" | gzip -n -9 > "$ARCHIVE"
SHA=$(sha256sum "$ARCHIVE" | awk '{print $1}')
SIZE=$(stat -c %s "$ARCHIVE")
printf '%s  %s\n' "$SHA" "$(basename "$ARCHIVE")" > "$OUTPUT/SHA256SUMS"
cat > "$OUTPUT/release-manifest.json" <<JSON
{
  "schema_version": 1,
  "project": "vocotype-linux",
  "version": "$VERSION",
  "commit": "$COMMIT",
  "artifacts": [
    {"path": "$(basename "$ARCHIVE")", "size": $SIZE, "sha256": "$SHA"}
  ]
}
JSON
echo "$ARCHIVE"

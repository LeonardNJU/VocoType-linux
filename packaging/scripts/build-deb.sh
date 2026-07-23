#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
OUT=${1:-"$ROOT/dist/packages"}
. "$ROOT/packaging/scripts/package-common.sh"
FLAVOR=${2:-${VOCOTYPE_PACKAGE_FLAVOR:-universal}}
FLAVOR=$(vocotype_flavor "$FLAVOR")
PACKAGE_NAME=$(vocotype_flavor_field "$FLAVOR" package_name)
VERSION=$(vocotype_version "$ROOT")
DEBIAN_VERSION=$(vocotype_version_field "$VERSION" debian)
BUNDLE=${VOCOTYPE_STREAMING_BUNDLE_DIR:?VOCOTYPE_STREAMING_BUNDLE_DIR is required for complete packages}
command -v dpkg-buildpackage >/dev/null 2>&1 || { echo "dpkg-buildpackage is required" >&2; exit 127; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$OUT"
"$ROOT/packaging/scripts/build-source-release.sh" --source-only --output "$work/release"
base="$work/release/VocoType-linux-$VERSION.tar.gz"
complete="$work/VocoType-linux-$VERSION.tar.gz"
"$ROOT/packaging/scripts/prepare-complete-source.sh" \
  --source "$base" --native-bundle "$BUNDLE" \
  --flavor "$FLAVOR" --output "$complete"
tar -xzf "$complete" -C "$work"
src="$work/VocoType-linux-$VERSION"
cp -a "$src/packaging/debian" "$src/debian"
"$ROOT/packaging/scripts/render-package-metadata.sh"   --format debian --flavor "$FLAVOR"   --template "$ROOT/packaging/debian/control"   --output "$src/debian/control"
sed -i "1s/([^)]*)/($DEBIAN_VERSION-1)/" "$src/debian/changelog"
(
  cd "$src"
  PACKAGE_NAME="$PACKAGE_NAME" PACKAGE_FLAVOR="$FLAVOR"     dpkg-buildpackage -us -uc -b
)
find "$work" -maxdepth 1 -type f -name '*.deb' -exec cp -f {} "$OUT/" \;
for artifact in "$work"/*.changes "$work"/*.buildinfo; do
  [[ -f "$artifact" ]] || continue
  basename=$(basename "$artifact")
  renamed=${basename/#vocotype-linux_/${PACKAGE_NAME}_}
  cp -f "$artifact" "$OUT/$renamed"
done
echo "DEB flavor=$FLAVOR package=$PACKAGE_NAME artifacts written to $OUT"

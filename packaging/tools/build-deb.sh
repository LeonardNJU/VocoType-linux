#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
OUT=${1:-"$ROOT/dist/packages"}
FLAVOR=${2:-${VOCOTYPE_PACKAGE_FLAVOR:-universal}}
FLAVOR=$(python3 "$ROOT/packaging/tools/package-flavor.py" "$FLAVOR" --field flavor)
PACKAGE_NAME=$(python3 "$ROOT/packaging/tools/package-flavor.py" "$FLAVOR" --field package_name)
VERSION=$(python3 "$ROOT/packaging/tools/versioning.py" "$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/vocotype_version.py")" --field python)
DEBIAN_VERSION=$(python3 "$ROOT/packaging/tools/versioning.py" "$VERSION" --field debian)
BUNDLE=${VOCOTYPE_STREAMING_BUNDLE_DIR:?VOCOTYPE_STREAMING_BUNDLE_DIR is required for complete packages}
WHEELHOUSE=${VOCOTYPE_WHEELHOUSE_DIR:?VOCOTYPE_WHEELHOUSE_DIR is required for complete packages}
command -v dpkg-buildpackage >/dev/null 2>&1 || { echo "dpkg-buildpackage is required" >&2; exit 127; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$OUT"
python3 "$ROOT/packaging/tools/build-release.py" --source-only --output "$work/release"
base="$work/release/VocoType-linux-$VERSION.tar.gz"
complete="$work/VocoType-linux-$VERSION.tar.gz"
python3 "$ROOT/packaging/tools/prepare-complete-source.py" \
  --source "$base" --native-bundle "$BUNDLE" --wheelhouse "$WHEELHOUSE" \
  --flavor "$FLAVOR" --output "$complete"
tar -xzf "$complete" -C "$work"
src="$work/VocoType-linux-$VERSION"
cp -a "$src/packaging/debian" "$src/debian"
python3 "$ROOT/packaging/tools/render-package-metadata.py"   --format debian --flavor "$FLAVOR"   --template "$ROOT/packaging/debian/control"   --output "$src/debian/control"
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

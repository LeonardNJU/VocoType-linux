#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
OUT=${1:-"$ROOT/dist/packages"}
. "$ROOT/packaging/scripts/package-common.sh"
FLAVOR=${2:-${VOCOTYPE_PACKAGE_FLAVOR:-universal}}
FLAVOR=$(vocotype_flavor "$FLAVOR")
PACKAGE_NAME=$(vocotype_flavor_field "$FLAVOR" package_name)
VERSION=$(vocotype_version "$ROOT")
ARCH_VERSION=$(vocotype_version_field "$VERSION" arch)
BUNDLE=${VOCOTYPE_STREAMING_BUNDLE_DIR:?VOCOTYPE_STREAMING_BUNDLE_DIR is required for complete packages}
command -v makepkg >/dev/null 2>&1 || { echo "makepkg is required" >&2; exit 127; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$OUT"
"$ROOT/packaging/scripts/build-source-release.sh" --source-only --output "$work/release"
base="$work/release/VocoType-linux-$VERSION.tar.gz"
archive="$work/VocoType-linux-$ARCH_VERSION.tar.gz"
"$ROOT/packaging/scripts/prepare-complete-source.sh" \
  --source "$base" --native-bundle "$BUNDLE" \
  --flavor "$FLAVOR" --output "$archive"
sha=$(sha256sum "$archive" | awk '{print $1}')
"$ROOT/packaging/scripts/render-package-metadata.sh" \
  --format arch --flavor "$FLAVOR" \
  --template "$ROOT/packaging/arch/PKGBUILD.in" \
  --output "$work/PKGBUILD.flavor"
sed -e "s/@VERSION@/$ARCH_VERSION/g" -e "s/@SOURCE_SHA256@/$sha/g" \
  "$work/PKGBUILD.flavor" > "$work/PKGBUILD"
(
  cd "$work"
  makepkg --cleanbuild --force --noconfirm --nodeps
)
find "$work" -maxdepth 1 -type f -name '*.pkg.tar.*' -exec cp -f {} "$OUT/" \;
echo "Arch flavor=$FLAVOR package=$PACKAGE_NAME artifacts written to $OUT"

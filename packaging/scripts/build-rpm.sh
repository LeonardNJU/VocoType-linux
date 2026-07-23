#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
OUT=${1:-"$ROOT/dist/packages"}
. "$ROOT/packaging/scripts/package-common.sh"
FLAVOR=${2:-${VOCOTYPE_PACKAGE_FLAVOR:-universal}}
FLAVOR=$(vocotype_flavor "$FLAVOR")
PACKAGE_NAME=$(vocotype_flavor_field "$FLAVOR" package_name)
VERSION=$(vocotype_version "$ROOT")
RPM_VERSION=$(vocotype_version_field "$VERSION" rpm_version)
RPM_RELEASE=$(vocotype_version_field "$VERSION" rpm_release)
BUNDLE=${VOCOTYPE_STREAMING_BUNDLE_DIR:?VOCOTYPE_STREAMING_BUNDLE_DIR is required for complete packages}
command -v rpmbuild >/dev/null 2>&1 || { echo "rpmbuild is required" >&2; exit 127; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/rpmbuild"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS} "$OUT"
"$ROOT/packaging/scripts/build-source-release.sh" --source-only --output "$work/release"
base="$work/release/VocoType-linux-$VERSION.tar.gz"
complete="$work/rpmbuild/SOURCES/VocoType-linux-$VERSION.tar.gz"
"$ROOT/packaging/scripts/prepare-complete-source.sh" \
  --source "$base" --native-bundle "$BUNDLE" \
  --flavor "$FLAVOR" --output "$complete"
"$ROOT/packaging/scripts/render-package-metadata.sh" \
  --format rpm --flavor "$FLAVOR" \
  --template "$ROOT/packaging/rpm/vocotype.spec.in" \
  --output "$work/rpmbuild/SPECS/vocotype.flavor.spec"
sed \
  -e "s/@VERSION@/$RPM_VERSION/g" \
  -e "s/@RELEASE@/$RPM_RELEASE/g" \
  -e "s/@SOURCE_VERSION@/$VERSION/g" \
  "$work/rpmbuild/SPECS/vocotype.flavor.spec" > "$work/rpmbuild/SPECS/vocotype.spec"
rpmbuild -ba --define "_topdir $work/rpmbuild" "$work/rpmbuild/SPECS/vocotype.spec"
find "$work/rpmbuild/RPMS" "$work/rpmbuild/SRPMS" -type f -name '*.rpm' -exec cp -f {} "$OUT/" \;
echo "RPM flavor=$FLAVOR package=$PACKAGE_NAME artifacts written to $OUT"

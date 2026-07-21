#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
OUT=${1:-"$ROOT/dist/packages"}
FLAVOR=${2:-${VOCOTYPE_PACKAGE_FLAVOR:-universal}}
FLAVOR=$(python3 "$ROOT/packaging/tools/package-flavor.py" "$FLAVOR" --field flavor)
PACKAGE_NAME=$(python3 "$ROOT/packaging/tools/package-flavor.py" "$FLAVOR" --field package_name)
VERSION=$(python3 "$ROOT/packaging/tools/versioning.py" "$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/vocotype_version.py")" --field python)
RPM_VERSION=$(python3 "$ROOT/packaging/tools/versioning.py" "$VERSION" --field rpm_version)
RPM_RELEASE=$(python3 "$ROOT/packaging/tools/versioning.py" "$VERSION" --field rpm_release)
BUNDLE=${VOCOTYPE_STREAMING_BUNDLE_DIR:?VOCOTYPE_STREAMING_BUNDLE_DIR is required for complete packages}
WHEELHOUSE=${VOCOTYPE_WHEELHOUSE_DIR:?VOCOTYPE_WHEELHOUSE_DIR is required for complete packages}
command -v rpmbuild >/dev/null 2>&1 || { echo "rpmbuild is required" >&2; exit 127; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/rpmbuild"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS} "$OUT"
python3 "$ROOT/packaging/tools/build-release.py" --source-only --output "$work/release"
base="$work/release/VocoType-linux-$VERSION.tar.gz"
complete="$work/rpmbuild/SOURCES/VocoType-linux-$VERSION.tar.gz"
python3 "$ROOT/packaging/tools/prepare-complete-source.py" \
  --source "$base" --native-bundle "$BUNDLE" --wheelhouse "$WHEELHOUSE" \
  --output "$complete"
python3 "$ROOT/packaging/tools/render-package-metadata.py" \
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

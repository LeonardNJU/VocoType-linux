#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
OUT=${1:-"$ROOT/dist/packages"}
VERSION=$(sed -n 's/^__version__ = "\([0-9][0-9.]*\)"/\1/p' "$ROOT/vocotype_version.py")
command -v rpmbuild >/dev/null 2>&1 || { echo "rpmbuild is required" >&2; exit 127; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/rpmbuild"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS} "$OUT"
python3 "$ROOT/scripts/build-release.py" --source-only --output "$work/release"
cp "$work/release/VocoType-linux-$VERSION.tar.gz" "$work/rpmbuild/SOURCES/"
sed "s/@VERSION@/$VERSION/g" "$ROOT/packaging/rpm/vocotype.spec.in" > "$work/rpmbuild/SPECS/vocotype.spec"
rpmbuild -ba --define "_topdir $work/rpmbuild" "$work/rpmbuild/SPECS/vocotype.spec"
find "$work/rpmbuild/RPMS" "$work/rpmbuild/SRPMS" -type f -name '*.rpm' -exec cp -f {} "$OUT/" \;
echo "RPM artifacts written to $OUT"

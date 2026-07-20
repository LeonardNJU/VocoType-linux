#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
OUT=${1:-"$ROOT/dist/packages"}
VERSION=$(sed -n 's/^__version__ = "\([0-9][0-9.]*\)"/\1/p' "$ROOT/vocotype_version.py")
command -v dpkg-buildpackage >/dev/null 2>&1 || { echo "dpkg-buildpackage is required" >&2; exit 127; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$OUT"
python3 "$ROOT/scripts/build-release.py" --source-only --output "$work/release"
tar -xzf "$work/release/VocoType-linux-$VERSION.tar.gz" -C "$work"
src="$work/VocoType-linux-$VERSION"
cp -a "$src/packaging/debian" "$src/debian"
sed -i "1s/([^)]*)/($VERSION-1)/" "$src/debian/changelog"
(
  cd "$src"
  dpkg-buildpackage -us -uc -b
)
find "$work" -maxdepth 1 -type f \( -name '*.deb' -o -name '*.changes' -o -name '*.buildinfo' \) -exec cp -f {} "$OUT/" \;
echo "DEB artifacts written to $OUT"

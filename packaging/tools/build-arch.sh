#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
OUT=${1:-"$ROOT/dist/packages"}
VERSION=$(sed -n 's/^__version__ = "\([0-9][0-9.]*\)"/\1/p' "$ROOT/vocotype_version.py")
command -v makepkg >/dev/null 2>&1 || { echo "makepkg is required" >&2; exit 127; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$OUT"
python3 "$ROOT/packaging/tools/build-release.py" --source-only --output "$work/release"
archive="$work/release/VocoType-linux-$VERSION.tar.gz"
cp "$archive" "$work/"
sha=$(sha256sum "$archive" | awk '{print $1}')
sed -e "s/@VERSION@/$VERSION/g" -e "s/@SOURCE_SHA256@/$sha/g" \
  "$ROOT/packaging/arch/PKGBUILD.in" > "$work/PKGBUILD"
(
  cd "$work"
  makepkg --cleanbuild --force --noconfirm --nodeps
)
find "$work" -maxdepth 1 -type f -name '*.pkg.tar.*' -exec cp -f {} "$OUT/" \;
echo "Arch artifacts written to $OUT"

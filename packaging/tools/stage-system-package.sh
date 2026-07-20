#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: packaging/tools/stage-system-package.sh --destdir DIR [options]

Options:
  --prefix PREFIX       Installation prefix (default: /usr)
  --libdir LIBDIR       Library directory, absolute or relative to PREFIX (default: lib)
  --libexecdir DIR      Executable helper directory (default: PREFIX/libexec)
  --build-dir DIR       CMake build directory (default: build/package-fcitx)
  --skip-module-build   Stage files without compiling the Fcitx module
EOF
}

DESTDIR=""
PREFIX="/usr"
LIBDIR="lib"
LIBEXECDIR=""
BUILD_DIR=""
SKIP_MODULE_BUILD=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --destdir) DESTDIR="${2:?missing destination}"; shift 2 ;;
    --prefix) PREFIX="${2:?missing prefix}"; shift 2 ;;
    --libdir) LIBDIR="${2:?missing libdir}"; shift 2 ;;
    --libexecdir) LIBEXECDIR="${2:?missing libexecdir}"; shift 2 ;;
    --build-dir) BUILD_DIR="${2:?missing build dir}"; shift 2 ;;
    --skip-module-build) SKIP_MODULE_BUILD=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$DESTDIR" ]] || { echo "--destdir is required" >&2; exit 2; }
DESTDIR=$(readlink -m "$DESTDIR")
[[ "$DESTDIR" != "/" ]] || { echo "Refusing to stage directly into /" >&2; exit 2; }
[[ "$PREFIX" == /* ]] || { echo "--prefix must be absolute" >&2; exit 2; }
LIBEXECDIR=${LIBEXECDIR:-"$PREFIX/libexec"}
[[ "$LIBEXECDIR" == /* ]] || { echo "--libexecdir must be absolute" >&2; exit 2; }

PROJECT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
BUILD_DIR=${BUILD_DIR:-"$PROJECT_DIR/build/package-fcitx"}
VERSION=$(sed -n 's/^__version__ = "\([0-9][0-9.]*\)"/\1/p' "$PROJECT_DIR/vocotype_version.py")
[[ -n "$VERSION" ]] || { echo "Cannot determine VoCoType version" >&2; exit 1; }

source_root="$DESTDIR$PREFIX/share/vocotype"
mkdir -p "$source_root"

while IFS= read -r entry; do
  entry=${entry%%#*}
  entry=${entry%$'\r'}
  [[ -n "${entry//[[:space:]]/}" ]] || continue
  src="$PROJECT_DIR/$entry"
  [[ -e "$src" ]] || { echo "Release manifest entry does not exist: $entry" >&2; exit 1; }
  mkdir -p "$source_root/$(dirname "$entry")"
  cp -a "$src" "$source_root/$entry"
done < "$PROJECT_DIR/packaging/manifests/runtime-files.txt"

find "$source_root" -type d \( -name __pycache__ -o -name .pytest_cache -o -name build -o -name dist \) -prune -exec rm -rf {} + 2>/dev/null || true
find "$source_root" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
printf 'version=%s\nmanaged-by=native-package\n' "$VERSION" > "$source_root/.system-package"

install -Dm755 "$PROJECT_DIR/packaging/bin/vocotype-settings" "$DESTDIR$PREFIX/bin/vocotype-settings"
install -Dm755 "$PROJECT_DIR/packaging/bin/vocotype-fcitx5-backend" "$DESTDIR$PREFIX/bin/vocotype-fcitx5-backend"
install -Dm755 "$PROJECT_DIR/packaging/bin/vocotype-fcitx5-recorder" "$DESTDIR$PREFIX/bin/vocotype-fcitx5-recorder"
install -Dm755 "$PROJECT_DIR/packaging/bin/vocotype-ibus-engine" "$DESTDIR$LIBEXECDIR/vocotype-ibus-engine"
install -Dm644 "$PROJECT_DIR/fcitx5/data/vocotype.conf" "$DESTDIR$PREFIX/share/fcitx5/addon/vocotype.conf"
install -Dm644 "$PROJECT_DIR/packaging/systemd/vocotype-fcitx5-backend.service" "$DESTDIR$PREFIX/lib/systemd/user/vocotype-fcitx5-backend.service"
install -Dm644 "$PROJECT_DIR/data/applications/io.github.LeonardNJU.VoCoType.Settings.desktop" "$DESTDIR$PREFIX/share/applications/io.github.LeonardNJU.VoCoType.Settings.desktop"
install -Dm644 "$PROJECT_DIR/data/metainfo/io.github.LeonardNJU.VoCoType.metainfo.xml" "$DESTDIR$PREFIX/share/metainfo/io.github.LeonardNJU.VoCoType.metainfo.xml"
install -Dm644 "$PROJECT_DIR/site/icon-192.png" "$DESTDIR$PREFIX/share/icons/hicolor/192x192/apps/vocotype.png"
install -Dm644 "$PROJECT_DIR/LICENSE" "$DESTDIR$PREFIX/share/licenses/vocotype-linux/LICENSE"
install -Dm644 "$PROJECT_DIR/README.md" "$DESTDIR$PREFIX/share/doc/vocotype-linux/README.md"
install -Dm644 "$PROJECT_DIR/CHANGELOG.md" "$DESTDIR$PREFIX/share/doc/vocotype-linux/CHANGELOG.md"

mkdir -p "$DESTDIR$PREFIX/share/ibus/component"
sed \
  -e "s|VOCOTYPE_EXEC_PATH|$LIBEXECDIR/vocotype-ibus-engine|g" \
  -e "s|VOCOTYPE_VERSION|$VERSION|g" \
  "$PROJECT_DIR/ibus/data/vocotype.xml.in" > "$DESTDIR$PREFIX/share/ibus/component/vocotype.xml.tmp"
install -Dm644 "$DESTDIR$PREFIX/share/ibus/component/vocotype.xml.tmp" "$DESTDIR$PREFIX/share/ibus/component/vocotype.xml"
rm -f "$DESTDIR$PREFIX/share/ibus/component/vocotype.xml.tmp"

if [[ "$SKIP_MODULE_BUILD" != true ]]; then
  if [[ "$LIBDIR" == "$PREFIX/"* ]]; then
    cmake_libdir=${LIBDIR#"$PREFIX/"}
  elif [[ "$LIBDIR" == /* ]]; then
    cmake_libdir=${LIBDIR#/}
  else
    cmake_libdir=$LIBDIR
  fi
  cmake -S "$PROJECT_DIR/fcitx5/module" -B "$BUILD_DIR" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$PREFIX" \
    -DCMAKE_INSTALL_LIBDIR="$cmake_libdir" \
    -DVOCOTYPE_VERSION="$VERSION"
  cmake --build "$BUILD_DIR" --parallel "${JOBS:-2}"
  DESTDIR="$DESTDIR" cmake --install "$BUILD_DIR"
fi

echo "Staged VoCoType $VERSION under $DESTDIR"

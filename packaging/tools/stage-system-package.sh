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
  --flavor FLAVOR       universal, ibus, or fcitx5 (default: universal)
  --skip-module-build   Stage files without compiling the Fcitx module
  --require-streaming-bundle  Fail if the native 2-pass runtime is absent
  --skip-streaming-bundle     Never include the optional native 2-pass runtime
  --require-wheelhouse        Fail if the CI-built Python wheelhouse is absent
  --skip-wheelhouse           Never include a Python wheelhouse
EOF
}

DESTDIR=""
PREFIX="/usr"
LIBDIR="lib"
LIBEXECDIR=""
BUILD_DIR=""
FLAVOR=${VOCOTYPE_PACKAGE_FLAVOR:-universal}
SKIP_MODULE_BUILD=false
REQUIRE_STREAMING_BUNDLE=${VOCOTYPE_REQUIRE_STREAMING_BUNDLE:-0}
SKIP_STREAMING_BUNDLE=${VOCOTYPE_SKIP_STREAMING_BUNDLE:-0}
REQUIRE_WHEELHOUSE=${VOCOTYPE_REQUIRE_WHEELHOUSE:-0}
SKIP_WHEELHOUSE=${VOCOTYPE_SKIP_WHEELHOUSE:-0}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --destdir) DESTDIR="${2:?missing destination}"; shift 2 ;;
    --prefix) PREFIX="${2:?missing prefix}"; shift 2 ;;
    --libdir) LIBDIR="${2:?missing libdir}"; shift 2 ;;
    --libexecdir) LIBEXECDIR="${2:?missing libexecdir}"; shift 2 ;;
    --build-dir) BUILD_DIR="${2:?missing build dir}"; shift 2 ;;
    --flavor) FLAVOR="${2:?missing flavor}"; shift 2 ;;
    --skip-module-build) SKIP_MODULE_BUILD=true; shift ;;
    --require-streaming-bundle) REQUIRE_STREAMING_BUNDLE=1; shift ;;
    --skip-streaming-bundle) SKIP_STREAMING_BUNDLE=1; shift ;;
    --require-wheelhouse) REQUIRE_WHEELHOUSE=1; shift ;;
    --skip-wheelhouse) SKIP_WHEELHOUSE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$REQUIRE_STREAMING_BUNDLE" == "1" && "$SKIP_STREAMING_BUNDLE" == "1" ]]; then
  echo "--require-streaming-bundle and --skip-streaming-bundle are mutually exclusive" >&2
  exit 2
fi
if [[ "$REQUIRE_WHEELHOUSE" == "1" && "$SKIP_WHEELHOUSE" == "1" ]]; then
  echo "--require-wheelhouse and --skip-wheelhouse are mutually exclusive" >&2
  exit 2
fi
[[ -n "$DESTDIR" ]] || { echo "--destdir is required" >&2; exit 2; }
DESTDIR=$(readlink -m "$DESTDIR")
[[ "$DESTDIR" != "/" ]] || { echo "Refusing to stage directly into /" >&2; exit 2; }
[[ "$PREFIX" == /* ]] || { echo "--prefix must be absolute" >&2; exit 2; }
LIBEXECDIR=${LIBEXECDIR:-"$PREFIX/libexec"}
[[ "$LIBEXECDIR" == /* ]] || { echo "--libexecdir must be absolute" >&2; exit 2; }

PROJECT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
FLAVOR=$(python3 "$PROJECT_DIR/packaging/tools/package-flavor.py" "$FLAVOR" --field flavor)
PACKAGE_NAME=$(python3 "$PROJECT_DIR/packaging/tools/package-flavor.py" "$FLAVOR" --field package_name)
INCLUDES_IBUS=$(python3 "$PROJECT_DIR/packaging/tools/package-flavor.py" "$FLAVOR" --field includes_ibus)
INCLUDES_FCITX5=$(python3 "$PROJECT_DIR/packaging/tools/package-flavor.py" "$FLAVOR" --field includes_fcitx5)
BUILD_DIR=${BUILD_DIR:-"$PROJECT_DIR/build/package-fcitx-$FLAVOR"}
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$PROJECT_DIR/vocotype_version.py")
[[ -n "$VERSION" ]] || { echo "Cannot determine VoCoType version" >&2; exit 1; }
CMAKE_VERSION=$(python3 "$PROJECT_DIR/packaging/tools/versioning.py" "$VERSION" --field rpm_version)

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
if [[ "$INCLUDES_IBUS" != true ]]; then
  rm -rf "$source_root/ibus"
fi
if [[ "$INCLUDES_FCITX5" != true ]]; then
  rm -rf "$source_root/fcitx5"
fi
printf 'version=%s\nmanaged-by=native-package\nflavor=%s\npackage=%s\n' \
  "$VERSION" "$FLAVOR" "$PACKAGE_NAME" > "$source_root/.system-package"


wheelhouse=${VOCOTYPE_WHEELHOUSE_DIR:-"$PROJECT_DIR/vendor/wheelhouse"}
if [[ "$SKIP_WHEELHOUSE" == "1" ]]; then
  echo "Python runtime wheelhouse intentionally omitted from this staging tree" >&2
elif compgen -G "$wheelhouse/*.whl" >/dev/null; then
  python3 "$PROJECT_DIR/packaging/tools/audit-wheelhouse.py" "$wheelhouse"
  rm -rf "$source_root/wheelhouse"
  mkdir -p "$source_root/wheelhouse"
  cp -a "$wheelhouse"/*.whl "$source_root/wheelhouse/"
elif [[ "$REQUIRE_WHEELHOUSE" == "1" ]]; then
  echo "Required Python runtime wheelhouse is missing: $wheelhouse" >&2
  exit 1
else
  echo "Python runtime wheelhouse not present; source/development staging only" >&2
fi

install -Dm755 "$PROJECT_DIR/packaging/bin/vocotype-settings" "$DESTDIR$PREFIX/bin/vocotype-settings"
if [[ "$INCLUDES_FCITX5" == true ]]; then
  install -Dm755 "$PROJECT_DIR/packaging/bin/vocotype-fcitx5-backend" "$DESTDIR$PREFIX/bin/vocotype-fcitx5-backend"
  install -Dm755 "$PROJECT_DIR/packaging/bin/vocotype-fcitx5-recorder" "$DESTDIR$PREFIX/bin/vocotype-fcitx5-recorder"
fi
if [[ "$INCLUDES_IBUS" == true ]]; then
  install -Dm755 "$PROJECT_DIR/packaging/bin/vocotype-ibus-engine" "$DESTDIR$LIBEXECDIR/vocotype-ibus-engine"
fi

streaming_bundle=${VOCOTYPE_STREAMING_BUNDLE_DIR:-"$PROJECT_DIR/native/streaming_worker/build/bundle"}
if [[ "$SKIP_STREAMING_BUNDLE" == "1" ]]; then
  echo "Optional native streaming bundle intentionally omitted from this package" >&2
elif [[ -x "$streaming_bundle/bin/vocotype-streaming-worker" && -d "$streaming_bundle/lib" ]]; then
  if [[ "$LIBDIR" == /* ]]; then
    runtime_streaming_libdir="$LIBDIR/vocotype"
  else
    runtime_streaming_libdir="$PREFIX/$LIBDIR/vocotype"
  fi
  streaming_libdir="$DESTDIR$runtime_streaming_libdir"
  mkdir -p "$streaming_libdir" "$DESTDIR$LIBEXECDIR"
  install -m755 "$streaming_bundle/bin/vocotype-streaming-worker" \
    "$streaming_libdir/vocotype-streaming-worker"
  cp -a "$streaming_bundle/lib/." "$streaming_libdir/"
  if [[ -d "$streaming_bundle/share/licenses" ]]; then
    mkdir -p "$DESTDIR$PREFIX/share/licenses/vocotype-linux/native-streaming"
    cp -a "$streaming_bundle/share/licenses/." \
      "$DESTDIR$PREFIX/share/licenses/vocotype-linux/native-streaming/"
  fi
  if [[ "$LIBEXECDIR/vocotype-streaming-worker" != "$runtime_streaming_libdir/vocotype-streaming-worker" ]]; then
    streaming_link_target=$(realpath -m --relative-to="$LIBEXECDIR" \
      "$runtime_streaming_libdir/vocotype-streaming-worker")
    ln -sfn "$streaming_link_target" \
      "$DESTDIR$LIBEXECDIR/vocotype-streaming-worker"
  fi
elif [[ "$REQUIRE_STREAMING_BUNDLE" == "1" ]]; then
  echo "Required native streaming bundle is missing: $streaming_bundle" >&2
  exit 1
else
  echo "Native streaming bundle not present; package will keep 2-pass preview unavailable" >&2
fi
if [[ "$INCLUDES_FCITX5" == true ]]; then
  install -Dm644 "$PROJECT_DIR/fcitx5/data/vocotype.conf" "$DESTDIR$PREFIX/share/fcitx5/addon/vocotype.conf"
  install -Dm644 "$PROJECT_DIR/packaging/systemd/vocotype-fcitx5-backend.service" "$DESTDIR$PREFIX/lib/systemd/user/vocotype-fcitx5-backend.service"
fi
install -Dm644 "$PROJECT_DIR/data/applications/io.github.LeonardNJU.VoCoType.Settings.desktop" "$DESTDIR$PREFIX/share/applications/io.github.LeonardNJU.VoCoType.Settings.desktop"
install -Dm644 "$PROJECT_DIR/data/metainfo/io.github.LeonardNJU.VoCoType.metainfo.xml" "$DESTDIR$PREFIX/share/metainfo/io.github.LeonardNJU.VoCoType.metainfo.xml"
install -Dm644 "$PROJECT_DIR/site/icon-192.png" "$DESTDIR$PREFIX/share/icons/hicolor/192x192/apps/vocotype.png"
install -Dm644 "$PROJECT_DIR/LICENSE" "$DESTDIR$PREFIX/share/licenses/vocotype-linux/LICENSE"
install -Dm644 "$PROJECT_DIR/README.md" "$DESTDIR$PREFIX/share/doc/vocotype-linux/README.md"
install -Dm644 "$PROJECT_DIR/CHANGELOG.md" "$DESTDIR$PREFIX/share/doc/vocotype-linux/CHANGELOG.md"

if [[ "$INCLUDES_IBUS" == true ]]; then
  mkdir -p "$DESTDIR$PREFIX/share/ibus/component"
  sed \
    -e "s|VOCOTYPE_EXEC_PATH|$LIBEXECDIR/vocotype-ibus-engine|g" \
    -e "s|VOCOTYPE_VERSION|$VERSION|g" \
    "$PROJECT_DIR/ibus/data/vocotype.xml.in" > "$DESTDIR$PREFIX/share/ibus/component/vocotype.xml.tmp"
  install -Dm644 "$DESTDIR$PREFIX/share/ibus/component/vocotype.xml.tmp" "$DESTDIR$PREFIX/share/ibus/component/vocotype.xml"
  rm -f "$DESTDIR$PREFIX/share/ibus/component/vocotype.xml.tmp"
fi

if [[ "$INCLUDES_FCITX5" == true && "$SKIP_MODULE_BUILD" != true ]]; then
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
    -DVOCOTYPE_VERSION="$CMAKE_VERSION"
  cmake --build "$BUILD_DIR" --parallel "${JOBS:-2}"
  DESTDIR="$DESTDIR" cmake --install "$BUILD_DIR"
fi

echo "Staged VoCoType $VERSION flavor=$FLAVOR package=$PACKAGE_NAME under $DESTDIR"

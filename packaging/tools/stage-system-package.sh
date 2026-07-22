#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'HELP'
Usage: packaging/tools/stage-system-package.sh --destdir DIR [options]

Options:
  --prefix PREFIX       Installation prefix (default: /usr)
  --libdir LIBDIR       Library directory (default: lib)
  --libexecdir DIR      Native helper directory (default: PREFIX/libexec)
  --build-dir DIR       Build root
  --flavor FLAVOR       universal, ibus, or fcitx5
  --package-manager MGR apt, dnf, pacman, or auto
  --skip-module-build   Do not build the Fcitx module
  --require-streaming-bundle  Fail if the native ASR bundle is absent
  --skip-streaming-bundle     Do not stage the native ASR bundle
HELP
}

DESTDIR=""
PREFIX=/usr
LIBDIR=lib
LIBEXECDIR=""
BUILD_DIR=""
FLAVOR=${VOCOTYPE_PACKAGE_FLAVOR:-universal}
PACKAGE_MANAGER=${VOCOTYPE_PACKAGE_MANAGER:-auto}
SKIP_MODULE_BUILD=false
REQUIRE_STREAMING_BUNDLE=${VOCOTYPE_REQUIRE_STREAMING_BUNDLE:-0}
SKIP_STREAMING_BUNDLE=${VOCOTYPE_SKIP_STREAMING_BUNDLE:-0}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --destdir) DESTDIR="${2:?missing destination}"; shift 2 ;;
    --prefix) PREFIX="${2:?missing prefix}"; shift 2 ;;
    --libdir) LIBDIR="${2:?missing libdir}"; shift 2 ;;
    --libexecdir) LIBEXECDIR="${2:?missing libexecdir}"; shift 2 ;;
    --build-dir) BUILD_DIR="${2:?missing build dir}"; shift 2 ;;
    --flavor) FLAVOR="${2:?missing flavor}"; shift 2 ;;
    --package-manager) PACKAGE_MANAGER="${2:?missing manager}"; shift 2 ;;
    --skip-module-build) SKIP_MODULE_BUILD=true; shift ;;
    --require-streaming-bundle) REQUIRE_STREAMING_BUNDLE=1; shift ;;
    --skip-streaming-bundle) SKIP_STREAMING_BUNDLE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$REQUIRE_STREAMING_BUNDLE" == 1 && "$SKIP_STREAMING_BUNDLE" == 1 ]]; then
  echo "--require-streaming-bundle and --skip-streaming-bundle are mutually exclusive" >&2
  exit 2
fi

[[ -n "$DESTDIR" ]] || { echo "--destdir is required" >&2; exit 2; }
DESTDIR=$(readlink -m "$DESTDIR")
[[ "$DESTDIR" != / ]] || { echo "Refusing to stage into /" >&2; exit 2; }
[[ "$PREFIX" == /* ]] || { echo "--prefix must be absolute" >&2; exit 2; }
LIBEXECDIR=${LIBEXECDIR:-"$PREFIX/libexec"}
[[ "$LIBEXECDIR" == /* ]] || { echo "--libexecdir must be absolute" >&2; exit 2; }

case "$PACKAGE_MANAGER" in
  auto)
    if command -v pacman >/dev/null 2>&1; then PACKAGE_MANAGER=pacman
    elif command -v dnf >/dev/null 2>&1; then PACKAGE_MANAGER=dnf
    elif command -v apt-get >/dev/null 2>&1; then PACKAGE_MANAGER=apt
    else PACKAGE_MANAGER=""; fi ;;
  apt|dnf|pacman|"") ;;
  *) echo "invalid package manager" >&2; exit 2 ;;
esac

PROJECT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
. "$PROJECT_DIR/packaging/tools/package-common.sh"
FLAVOR=$(vocotype_flavor "$FLAVOR")
PACKAGE_NAME=$(vocotype_flavor_field "$FLAVOR" package_name)
INCLUDES_IBUS=$(vocotype_flavor_field "$FLAVOR" includes_ibus)
INCLUDES_FCITX5=$(vocotype_flavor_field "$FLAVOR" includes_fcitx5)
VERSION=$(vocotype_version "$PROJECT_DIR")
CMAKE_VERSION=$(vocotype_version_field "$VERSION" rpm_version)
BUILD_DIR=${BUILD_DIR:-"$PROJECT_DIR/build/package-$FLAVOR"}
DESKTOP_BUILD="$BUILD_DIR/desktop"
FCITX_BUILD="$BUILD_DIR/fcitx"

# Minimal immutable package metadata for the compiled runtime.
# are installed into the runtime package.
source_root="$DESTDIR$PREFIX/share/vocotype"
mkdir -p "$source_root"
printf 'version=%s\nmanaged-by=native-package\nflavor=%s\npackage=%s\nruntime=native\n' \
  "$VERSION" "$FLAVOR" "$PACKAGE_NAME" > "$source_root/.system-package"
[[ -z "$PACKAGE_MANAGER" ]] || printf 'manager=%s\n' "$PACKAGE_MANAGER" >> "$source_root/.system-package"
install -Dm755 "$PROJECT_DIR/installers/install-native-user.sh" \
  "$source_root/installers/install-native-user.sh"
install -Dm755 "$PROJECT_DIR/installers/uninstall-native-user.sh" \
  "$source_root/installers/uninstall-native-user.sh"

# Compile the desktop/runtime-facing programs for the target distribution.
cmake -S "$PROJECT_DIR/native/desktop" -B "$DESKTOP_BUILD" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$PREFIX" \
  -DCMAKE_INSTALL_BINDIR=bin \
  -DCMAKE_INSTALL_LIBEXECDIR="$LIBEXECDIR" \
  -DVOCOTYPE_VERSION="$VERSION" \
  -DVOCOTYPE_BUILD_SETTINGS=ON \
  -DVOCOTYPE_BUILD_IBUS="$INCLUDES_IBUS" \
  -DVOCOTYPE_BUILD_RIME="$INCLUDES_IBUS" \
  -DBUILD_TESTING=OFF
cmake --build "$DESKTOP_BUILD" --parallel "${JOBS:-2}"
DESTDIR="$DESTDIR" cmake --install "$DESKTOP_BUILD"

# The audited FunASR/ONNX bundle owns the C++ core and both model workers.
streaming_bundle=${VOCOTYPE_STREAMING_BUNDLE_DIR:-"$PROJECT_DIR/native/streaming_worker/build/bundle"}
if [[ "$SKIP_STREAMING_BUNDLE" == 1 ]]; then
  echo "native ASR bundle omitted" >&2
elif [[ -x "$streaming_bundle/bin/vocotype-core" && \
        -x "$streaming_bundle/bin/vocotype-streaming-worker" && \
        -x "$streaming_bundle/bin/vocotype-offline-worker" && \
        -d "$streaming_bundle/lib" ]]; then
  if [[ "$LIBDIR" == /* ]]; then runtime_libdir="$LIBDIR/vocotype"
  else runtime_libdir="$PREFIX/$LIBDIR/vocotype"; fi
  private_dir="$DESTDIR$runtime_libdir"
  mkdir -p "$private_dir" "$DESTDIR$LIBEXECDIR"
  for executable in vocotype-core vocotype-streaming-worker vocotype-offline-worker; do
    install -m755 "$streaming_bundle/bin/$executable" "$private_dir/$executable"
    if [[ "$LIBEXECDIR/$executable" != "$runtime_libdir/$executable" ]]; then
      cat > "$DESTDIR$LIBEXECDIR/$executable" <<LAUNCHER
#!/usr/bin/env bash
set -euo pipefail
exec "$runtime_libdir/$executable" "\$@"
LAUNCHER
      chmod 0755 "$DESTDIR$LIBEXECDIR/$executable"
    fi
  done
  cp -a "$streaming_bundle/lib/." "$private_dir/"
  (
    cd "$private_dir"
    find . -maxdepth 1 \( -type f -o -type l \) ! -name .native-payload.sha256 -print0 \
      | sort -z | xargs -0 sha256sum > .native-payload.sha256
  )
  if [[ -d "$streaming_bundle/share/licenses" ]]; then
    mkdir -p "$DESTDIR$PREFIX/share/licenses/vocotype-linux/native-streaming"
    cp -a "$streaming_bundle/share/licenses/." \
      "$DESTDIR$PREFIX/share/licenses/vocotype-linux/native-streaming/"
  fi
elif [[ "$REQUIRE_STREAMING_BUNDLE" == 1 ]]; then
  echo "Required native ASR bundle is missing: $streaming_bundle" >&2
  exit 1
else
  echo "Native ASR bundle missing; development staging is incomplete" >&2
fi

if [[ "$INCLUDES_FCITX5" == true ]]; then
  install -Dm755 "$PROJECT_DIR/packaging/bin/vocotype-fcitx5-backend" \
    "$DESTDIR$PREFIX/bin/vocotype-fcitx5-backend"
  install -Dm755 "$PROJECT_DIR/packaging/bin/vocotype-fcitx5-recorder" \
    "$DESTDIR$PREFIX/bin/vocotype-fcitx5-recorder"
  install -Dm644 "$PROJECT_DIR/fcitx5/data/vocotype.conf" \
    "$DESTDIR$PREFIX/share/fcitx5/addon/vocotype.conf"
  install -Dm644 "$PROJECT_DIR/packaging/systemd/vocotype-fcitx5-backend.service" \
    "$DESTDIR$PREFIX/lib/systemd/user/vocotype-fcitx5-backend.service"
  if [[ "$SKIP_MODULE_BUILD" != true ]]; then
    if [[ "$LIBDIR" == "$PREFIX/"* ]]; then cmake_libdir=${LIBDIR#"$PREFIX/"}
    elif [[ "$LIBDIR" == /* ]]; then cmake_libdir=${LIBDIR#/}
    else cmake_libdir=$LIBDIR; fi
    cmake -S "$PROJECT_DIR/fcitx5/module" -B "$FCITX_BUILD" \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX="$PREFIX" \
      -DCMAKE_INSTALL_LIBDIR="$cmake_libdir" \
      -DVOCOTYPE_VERSION="$CMAKE_VERSION"
    cmake --build "$FCITX_BUILD" --parallel "${JOBS:-2}"
    DESTDIR="$DESTDIR" cmake --install "$FCITX_BUILD"
  fi
fi

if [[ "$INCLUDES_IBUS" == true ]]; then
  mkdir -p "$DESTDIR$PREFIX/share/ibus/component"
  sed -e "s|VOCOTYPE_EXEC_PATH|$LIBEXECDIR/vocotype-ibus-engine|g" \
      -e "s|VOCOTYPE_VERSION|$VERSION|g" \
      "$PROJECT_DIR/ibus/data/vocotype.xml.in" > \
      "$DESTDIR$PREFIX/share/ibus/component/vocotype.xml"
fi

install -Dm644 "$PROJECT_DIR/data/applications/io.github.LeonardNJU.VoCoType.Settings.desktop" \
  "$DESTDIR$PREFIX/share/applications/io.github.LeonardNJU.VoCoType.Settings.desktop"
install -Dm644 "$PROJECT_DIR/data/metainfo/io.github.LeonardNJU.VoCoType.metainfo.xml" \
  "$DESTDIR$PREFIX/share/metainfo/io.github.LeonardNJU.VoCoType.metainfo.xml"
install -Dm644 "$PROJECT_DIR/site/icon-192.png" \
  "$DESTDIR$PREFIX/share/icons/hicolor/192x192/apps/vocotype.png"
install -Dm644 "$PROJECT_DIR/LICENSE" "$DESTDIR$PREFIX/share/licenses/vocotype-linux/LICENSE"
install -Dm644 "$PROJECT_DIR/README.md" "$DESTDIR$PREFIX/share/doc/vocotype-linux/README.md"
install -Dm644 "$PROJECT_DIR/CHANGELOG.md" "$DESTDIR$PREFIX/share/doc/vocotype-linux/CHANGELOG.md"

echo "Staged native-only VoCoType $VERSION flavor=$FLAVOR under $DESTDIR"

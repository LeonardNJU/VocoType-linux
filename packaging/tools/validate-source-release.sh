#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
. "$ROOT/packaging/tools/package-common.sh"
RELEASE_DIR="$ROOT/dist/release"
EXPECTED_VERSION=""
EXPECTED_COMMIT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --release-dir) RELEASE_DIR=${2:?}; shift 2 ;;
    --expected-version) EXPECTED_VERSION=${2:?}; shift 2 ;;
    --expected-commit) EXPECTED_COMMIT=${2:?}; shift 2 ;;
    -h|--help) echo "Usage: $0 [--release-dir DIR] [--expected-version V] [--expected-commit SHA]"; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
VERSION=$(vocotype_version "$ROOT")
[[ -z "$EXPECTED_VERSION" || "$EXPECTED_VERSION" == "$VERSION" ]] || { echo "Version mismatch" >&2; exit 1; }
ARCHIVE="$RELEASE_DIR/VocoType-linux-$VERSION.tar.gz"
[[ -f "$ARCHIVE" && -f "$RELEASE_DIR/SHA256SUMS" && -f "$RELEASE_DIR/release-manifest.json" ]] || { echo "Incomplete source release" >&2; exit 1; }
(cd "$RELEASE_DIR" && sha256sum -c SHA256SUMS)
declare -A members=()
while IFS= read -r member; do
  members["$member"]=1
done < <(tar -tzf "$ARCHIVE")
for required in \
  VERSION native/core/CMakeLists.txt native/desktop/CMakeLists.txt \
  native/desktop/src/settings_main.cpp native/desktop/src/ibus_main.cpp \
  native/desktop/src/model_manager_main.cpp \
  native/desktop/src/hotkey.cpp \
  native/desktop/include/vocotype/desktop/hotkey.hpp \
  fcitx5/module/vocotype_module.cpp \
  feedback_service/CMakeLists.txt feedback_service/src/main.cpp \
  installers/install-native-user.sh packaging/tools/stage-system-package.sh \
  flake.nix flake.lock nix/package.nix tests/hotkey-settings.sh \
  docs/getting-started/nix.md docs/guides/shortcuts.md; do
  expected="VocoType-linux-$VERSION/$required"
  [[ -n ${members[$expected]+x} ]] || {
    echo "Missing source member: $required" >&2
    exit 1
  }
done
for member in "${!members[@]}"; do
  case "$member" in
    VocoType-linux-*/app/*|VocoType-linux-*/settings_center/*|\
    VocoType-linux-*/fcitx5/backend/*|VocoType-linux-*/ibus/engine.py|\
    VocoType-linux-*/ibus/factory.py|VocoType-linux-*/ibus/main.py|\
    VocoType-linux-*/ibus/rime_runtime.py|*.py|*/pyproject.toml|\
    */uv.lock|*/requirements*.txt)
      echo "Source release contains forbidden Python member: $member" >&2
      exit 1
      ;;
  esac
done
if [[ -n "$EXPECTED_COMMIT" ]]; then
  grep -Fq '"commit": "'"$EXPECTED_COMMIT"'"' "$RELEASE_DIR/release-manifest.json" || { echo "Commit mismatch" >&2; exit 1; }
fi
echo "SOURCE_RELEASE_OK version=$VERSION"

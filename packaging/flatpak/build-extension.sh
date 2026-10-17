#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
OUTPUT="$ROOT/build/flatpak-vocotype-addon"
INSTALL=false
while (( $# )); do
  case "$1" in
    --output) OUTPUT=$2; shift 2 ;;
    --install) INSTALL=true; shift ;;
    -h|--help) echo "Usage: $0 [--output DIR] [--install]"; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
command -v flatpak-builder >/dev/null || {
  echo "Install flatpak-builder first." >&2; exit 78;
}
mkdir -p "$OUTPUT"
OUTPUT=$(realpath "$OUTPUT")
STAGING=$(mktemp -d)
trap 'rm -rf "$STAGING"' EXIT
mkdir -p "$STAGING/source/src/integrations/fcitx5" "$STAGING/source/src/common"
mkdir -p "$STAGING/bridges"
cp "$ROOT/VERSION" "$STAGING/source/"
cp -a "$ROOT/src/integrations/fcitx5/module" "$STAGING/source/src/integrations/fcitx5/"
cp -a "$ROOT/src/integrations/fcitx5/common" "$STAGING/source/src/integrations/fcitx5/"
cp -a "$ROOT/src/common/include" "$STAGING/source/src/common/"
cp "$ROOT/packaging/flatpak/host-recorder.sh" "$STAGING/bridges/"
cp "$ROOT/packaging/flatpak/host-backend.sh" "$STAGING/bridges/"
cp "$ROOT/packaging/flatpak/host-settings.sh" "$STAGING/bridges/"
cp "$ROOT/src/integrations/fcitx5/data/vocotype.conf" "$STAGING/bridges/"
cp "$ROOT/packaging/flatpak/org.fcitx.Fcitx5.Addon.VocoType.yaml" "$STAGING/"
flatpak-builder --force-clean --repo="$OUTPUT/repo" "$OUTPUT/build"   "$STAGING/org.fcitx.Fcitx5.Addon.VocoType.yaml"
if [[ "$INSTALL" == true ]]; then
  flatpak --user remote-add --if-not-exists vocotype-local "file://$OUTPUT/repo"
  flatpak --user install --noninteractive --or-update vocotype-local     org.fcitx.Fcitx5.Addon.VocoType
fi
echo "Addon repository: $OUTPUT/repo"
echo "Host setup and permissions: docs/integrations/flatpak-fcitx5.md"

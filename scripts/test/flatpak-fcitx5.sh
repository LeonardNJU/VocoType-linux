#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
ADDON="$ROOT/packaging/flatpak"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin"
# No Flatpak SDK is needed to inspect the generated addon staging tree.
cat > "$TMP/bin/flatpak-builder" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
manifest=""
for item in "$@"; do manifest=$item; done
test -f "$manifest"
base=$(dirname "$manifest")
test -f "$base/source/VERSION"
test -f "$base/source/src/integrations/fcitx5/module/vocotype_module.cpp"
test -f "$base/source/src/integrations/fcitx5/common/ipc_client.cpp"
test -f "$base/source/src/common/include/vocotype/common/spacing.hpp"
test -f "$base/bridges/host-recorder.sh"
test -f "$base/bridges/host-backend.sh"
test -f "$base/bridges/vocotype.conf"
grep -Fq 'runtime: org.fcitx.Fcitx5' "$manifest"
grep -Fq 'build-extension: true' "$manifest"
grep -Fq 'path: source' "$manifest"
grep -Fq 'path: bridges' "$manifest"
echo "addon staged correctly"
FAKE
chmod +x "$TMP/bin/flatpak-builder"
PATH="$TMP/bin:$PATH" bash "$ADDON/build-extension.sh" --output "$TMP/output" >"$TMP/build.log"
grep -Fq 'addon staged correctly' "$TMP/build.log"

# Host bridge only exposes its fixed command; no shell/eval argument injection.
cat > "$TMP/bin/flatpak-spawn" <<'FAKE'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$CAPTURE"
FAKE
chmod +x "$TMP/bin/flatpak-spawn"
export CAPTURE="$TMP/spawn.out"
PATH="$TMP/bin:$PATH" "$ADDON/host-recorder.sh" 'audio with spaces' '--flag'
grep -Fxq -- '--host' "$CAPTURE"
grep -Fxq -- 'audio with spaces' "$CAPTURE"
grep -Fxq -- '--flag' "$CAPTURE"
PATH="$TMP/bin:$PATH" "$ADDON/host-backend.sh"
grep -Fxq -- 'vocotype-flatpak-backend.service' "$CAPTURE"

# Permission must require deliberate opt-in. No app-wide filesystem access.
if PATH="$TMP/bin:$PATH" bash "$ADDON/enable-host-bridge.sh" >"$TMP/no-consent" 2>&1; then
  echo 'Missing consent was accepted' >&2; exit 1
fi
cat > "$TMP/bin/flatpak" <<'FAKE'
#!/usr/bin/env bash
if [[ "$1" == info ]]; then exit 0; fi
printf '%s\n' "$@" > "$CAPTURE"
FAKE
chmod +x "$TMP/bin/flatpak"
mkdir -p "$TMP/runtime"
CAPTURE="$TMP/permissions.out" XDG_RUNTIME_DIR="$TMP/runtime" PATH="$TMP/bin:$PATH"   bash "$ADDON/enable-host-bridge.sh" --accept-host-spawn >/dev/null
grep -Fxq -- '--filesystem=xdg-run/vocotype-flatpak' "$TMP/permissions.out"
grep -Fxq -- '--talk-name=org.freedesktop.Flatpak' "$TMP/permissions.out"
! grep -Eq -- '--filesystem=(home|host|host-root|/tmp)' "$TMP/permissions.out"
grep -Fq 'RuntimeDirectory=vocotype-flatpak' "$ROOT/scripts/install/common/install-native-user.sh"
grep -Fq 'flatpak-fcitx5)' "$ROOT/scripts/install/common/uninstall-native-user.sh"
test -d "$TMP/runtime/vocotype-flatpak"
bash -n "$ADDON/build-extension.sh" "$ADDON/enable-host-bridge.sh"   "$ROOT/scripts/install/common/install-native-user.sh"   "$ROOT/scripts/install/common/uninstall-native-user.sh"
echo 'FLATPAK_FCITX5_CONTRACT_OK'

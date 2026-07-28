#!/bin/bash
set -euo pipefail

if [[ $(uname -s) != Darwin ]]; then
  echo "macOS DMG must be built on macOS." >&2
  exit 2
fi

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
VERSION=$(tr -d '[:space:]' < "$ROOT/VERSION")
ARCH=$(uname -m)
JOBS=${JOBS:-$(sysctl -n hw.ncpu)}
BUILD_ROOT="$ROOT/build/macos-release"
CORE_BUILD="$BUILD_ROOT/core"
INPUT_BUILD="$BUILD_ROOT/input-method"
WORKER_BUNDLE="$ROOT/src/workers/funasr/build/bundle"
STAGE="$BUILD_ROOT/stage"
DMG_ROOT="$BUILD_ROOT/dmg-root"
APP="$STAGE/VoCoType-linux.app"
RESOURCES="$APP/Contents/Resources"
SETTINGS_APP="$STAGE/VoCoType-linux 设置.app"
SETTINGS_FRAMEWORKS="$SETTINGS_APP/Contents/Frameworks"
EMBEDDED_INPUT_ROOT="$SETTINGS_APP/Contents/Resources/InputMethod"
EMBEDDED_INPUT_APP="$EMBEDDED_INPUT_ROOT/VoCoType-linux.app"
DIST="$ROOT/dist"
DMG="$DIST/VoCoType-linux-${VERSION}-macOS-${ARCH}.dmg"
AUDIO_INPUT_ENTITLEMENTS="$SCRIPT_DIR/AudioInput.entitlements"
DMG_BACKGROUND="$SCRIPT_DIR/assets/dmg-background.png"
VOLUME_NAME="VoCoType-linux ${VERSION}"
RW_DMG="$BUILD_ROOT/VoCoType-linux-${VERSION}-rw.dmg"
DMG_MOUNT="$BUILD_ROOT/dmg-mount"

IDENTITY=${CODESIGN_IDENTITY:-}
SIGNING_KIND=developer-id
if [[ -z "$IDENTITY" ]]; then
  if [[ ${ALLOW_ADHOC_TEST:-0} != 1 ]]; then
    cat >&2 <<'MESSAGE'
Set CODESIGN_IDENTITY to an Apple Development or Developer ID Application
identity for a normally trusted build. For local compile/package diagnostics,
set ALLOW_ADHOC_TEST=1; that package is ad-hoc signed and may require the user
to bypass Gatekeeper manually.
MESSAGE
    exit 2
  fi
  IDENTITY=-
  SIGNING_KIND=adhoc-test
fi

cmake -S "$ROOT/src/core" -B "$CORE_BUILD" \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=ON
cmake --build "$CORE_BUILD" -j "$JOBS"
ctest --test-dir "$CORE_BUILD" --output-on-failure

cmake -S "$ROOT/src/integrations/macos" -B "$INPUT_BUILD" \
  -DCMAKE_BUILD_TYPE=Release
cmake --build "$INPUT_BUILD" -j "$JOBS"
ctest --test-dir "$INPUT_BUILD" --output-on-failure

if [[ ${SKIP_FUNASR_BUILD:-0} != 1 ]]; then
  JOBS="$JOBS" bash "$ROOT/src/workers/funasr/build.sh"
fi
for required in \
  "$WORKER_BUNDLE/bin/vocotype-streaming-worker" \
  "$WORKER_BUNDLE/bin/vocotype-offline-worker" \
  "$WORKER_BUNDLE/lib/libfunasr.dylib"; do
  [[ -f "$required" ]] || {
    echo "Missing native worker artifact: $required" >&2
    exit 2
  }
done

rm -rf "$STAGE" "$DMG_ROOT"
mkdir -p "$STAGE" "$DMG_ROOT" "$DIST"
ditto "$INPUT_BUILD/VoCoTypeLinuxInputMethod.app" "$APP"
ditto "$INPUT_BUILD/VoCoTypeLinuxSettings.app" "$SETTINGS_APP"
mkdir -p "$RESOURCES/bin" "$RESOURCES/lib" \
         "$RESOURCES/share/licenses/vocotype" "$SETTINGS_FRAMEWORKS"

cp "$CORE_BUILD/vocotype-core" "$RESOURCES/bin/"
cp "$INPUT_BUILD/vocotype-audio-recorder" "$RESOURCES/bin/"
cp "$INPUT_BUILD/vocotype-model-manager" "$RESOURCES/bin/"
cp "$INPUT_BUILD/vocotype-input-source-tool" "$RESOURCES/bin/"
cp "$WORKER_BUNDLE/bin/"* "$RESOURCES/bin/"
cp -a "$WORKER_BUNDLE/lib/." "$RESOURCES/lib/"
cp "$ROOT/resources/templates/terms.yaml" "$RESOURCES/share/terms.yaml"
cp "$ROOT/LICENSE" "$ROOT/THIRD_PARTY_NOTICES.md" \
   "$RESOURCES/share/licenses/vocotype/"

is_macho() {
  file -b "$1" 2>/dev/null | grep -q '^Mach-O'
}

dependency_destination() {
  local file=$1
  local base=$2
  case "$file" in
    "$SETTINGS_APP/"*) printf '%s/%s' "$SETTINGS_FRAMEWORKS" "$base" ;;
    *) printf '%s/%s' "$RESOURCES/lib" "$base" ;;
  esac
}

loader_reference() {
  local file=$1
  local base=$2
  case "$file" in
    "$RESOURCES/lib/"*) printf '@loader_path/%s' "$base" ;;
    "$RESOURCES/bin/"*) printf '@loader_path/../lib/%s' "$base" ;;
    "$SETTINGS_FRAMEWORKS/"*) printf '@loader_path/%s' "$base" ;;
    "$SETTINGS_APP/Contents/MacOS/"*) printf '@executable_path/../Frameworks/%s' "$base" ;;
    "$APP/Contents/MacOS/"*) printf '@executable_path/../Resources/lib/%s' "$base" ;;
    *) return 1 ;;
  esac
}

# Copy every non-system dylib closure and rewrite absolute Homebrew references.
changed=1
while [[ $changed == 1 ]]; do
  changed=0
  while IFS= read -r file; do
    is_macho "$file" || continue
    while IFS= read -r dependency; do
      case "$dependency" in
        /System/*|/usr/lib/*|@rpath/*|@loader_path/*|@executable_path/*) continue ;;
        /opt/homebrew/*|/usr/local/*)
          base=$(basename "$dependency")
          destination=$(dependency_destination "$file" "$base")
          if [[ ! -e "$destination" ]]; then
            cp -L "$dependency" "$destination"
            chmod u+w "$destination"
            changed=1
          fi
          replacement=$(loader_reference "$file" "$base")
          install_name_tool -change "$dependency" "$replacement" "$file"
          ;;
        *)
          echo "Unsupported non-system dependency: $file -> $dependency" >&2
          exit 2
          ;;
      esac
    done < <(otool -L "$file" | tail -n +2 | awk '{print $1}')
  done < <(find "$APP/Contents/MacOS" "$SETTINGS_APP/Contents/MacOS" \
             "$RESOURCES/bin" "$RESOURCES/lib" "$SETTINGS_FRAMEWORKS" \
             -type f -print)
done

# Give bundled libraries and executables relocatable loader search paths.
while IFS= read -r dylib; do
  is_macho "$dylib" || continue
  install_name_tool -id "@rpath/$(basename "$dylib")" "$dylib" 2>/dev/null || true
  install_name_tool -add_rpath '@loader_path' "$dylib" 2>/dev/null || true
done < <(find "$RESOURCES/lib" "$SETTINGS_FRAMEWORKS" -type f -print)
while IFS= read -r binary; do
  is_macho "$binary" || continue
  install_name_tool -add_rpath '@loader_path/../lib' "$binary" 2>/dev/null || true
done < <(find "$RESOURCES/bin" -type f -print)
install_name_tool -add_rpath '@executable_path/../Resources/lib' \
  "$APP/Contents/MacOS/VoCoTypeLinuxInputMethod" 2>/dev/null || true
install_name_tool -add_rpath '@executable_path/../Frameworks' \
  "$SETTINGS_APP/Contents/MacOS/VoCoTypeLinuxSettings" 2>/dev/null || true

# No packaged executable may retain a build-machine Homebrew path.
while IFS= read -r file; do
  is_macho "$file" || continue
  if otool -L "$file" | grep -Eq '/opt/homebrew|/usr/local'; then
    echo "Unbundled dependency remains in $file" >&2
    otool -L "$file" >&2
    exit 2
  fi
done < <(find "$APP/Contents/MacOS" "$SETTINGS_APP/Contents/MacOS" \
             "$RESOURCES/bin" "$RESOURCES/lib" "$SETTINGS_FRAMEWORKS" \
             -type f -print)

# Sign nested code from the inside out. A real identity enables the hardened
# runtime and secure timestamps; ad-hoc mode exists only for build diagnostics.
sign_macho() {
  local file=$1
  local runtime=${2:-0}
  local entitlements=${3:-}
  local args=(--force --sign "$IDENTITY")
  if [[ "$SIGNING_KIND" == adhoc-test ]]; then
    args+=(--timestamp=none)
  else
    args+=(--timestamp)
    if [[ "$runtime" == 1 ]]; then
      args+=(--options runtime)
    fi
  fi
  if [[ -n "$entitlements" ]]; then
    args+=(--entitlements "$entitlements")
  fi
  codesign "${args[@]}" "$file"
}

while IFS= read -r file; do
  is_macho "$file" || continue
  sign_macho "$file" 0
done < <(find "$RESOURCES/lib" "$SETTINGS_FRAMEWORKS" -type f -print)
while IFS= read -r file; do
  is_macho "$file" || continue
  if [[ $(basename "$file") == vocotype-audio-recorder ]]; then
    sign_macho "$file" 1 "$AUDIO_INPUT_ENTITLEMENTS"
  else
    sign_macho "$file" 1
  fi
done < <(find "$RESOURCES/bin" -type f -print)
# Sign the input-method payload first, then embed that signed bundle in the
# user-facing application and sign the outer bundle last.
sign_macho "$APP/Contents/MacOS/VoCoTypeLinuxInputMethod" 1   "$AUDIO_INPUT_ENTITLEMENTS"
sign_macho "$APP" 1 "$AUDIO_INPUT_ENTITLEMENTS"
codesign --verify --deep --strict --verbose=2 "$APP"

rm -rf "$EMBEDDED_INPUT_ROOT"
mkdir -p "$EMBEDDED_INPUT_ROOT"
ditto "$APP" "$EMBEDDED_INPUT_APP"
if [[ "$SIGNING_KIND" == adhoc-test ]]; then
  : > "$SETTINGS_APP/Contents/Resources/.adhoc-test"
fi
sign_macho "$SETTINGS_APP/Contents/MacOS/VoCoTypeLinuxSettings" 1   "$AUDIO_INPUT_ENTITLEMENTS"
sign_macho "$SETTINGS_APP" 1 "$AUDIO_INPUT_ENTITLEMENTS"
codesign --verify --deep --strict --verbose=2 "$SETTINGS_APP"

# Runtime smoke checks execute the staged binaries, not build-tree copies.
"$RESOURCES/bin/vocotype-streaming-worker" --help >/dev/null
"$RESOURCES/bin/vocotype-offline-worker" --help >/dev/null
"$RESOURCES/bin/vocotype-audio-recorder" --list-devices >/dev/null
"$RESOURCES/bin/vocotype-input-source-tool" --current >/dev/null

# Assemble a conventional drag-to-Applications disk image. The visible app is
# the settings center and carries a signed input-method payload internally.
ditto "$SETTINGS_APP" "$DMG_ROOT/VoCoType-linux.app"
ln -s /Applications "$DMG_ROOT/Applications"
ditto "$SCRIPT_DIR/uninstall.command" "$DMG_ROOT/卸载 VoCoType-linux.command"
mkdir -p "$DMG_ROOT/.background"
cp "$DMG_BACKGROUND" "$DMG_ROOT/.background/dmg-background.png"
if [[ "$SIGNING_KIND" == adhoc-test ]]; then
  : > "$DMG_ROOT/.adhoc-test"
  SIGNING_NOTE='此包采用 ad-hoc 签名且未经过 Apple 公证。若首次打开被 macOS 拦截，请先关闭提示，再前往“系统设置 → 隐私与安全性”，在“安全性”区域点击“仍要打开”，并在确认框中再次选择“仍要打开”。'
else
  SIGNING_NOTE='此包已使用 Apple 代码签名身份构建。首次使用时请允许麦克风权限。'
fi
cat > "$DMG_ROOT/使用说明.txt" <<TEXT
VoCoType-linux macOS 原生版

安装：
1. 将“VoCoType-linux”拖到右侧的“Applications”文件夹。
2. 从“应用程序”打开 VoCoType-linux。
3. 首次启动时，VoCoType-linux 会自动安装并激活内置输入法组件；无需切换当前键盘输入法。

快捷键：F9 语音输入；Shift+F9 长句润色；Ctrl+F9 语音编辑。

若首次打开被 Gatekeeper 拦截：
1. 先尝试打开一次 VoCoType-linux，然后关闭系统拦截提示；
2. 打开“系统设置 → 隐私与安全性”；
3. 在“安全性”区域找到被阻止的 VoCoType-linux 并点击“仍要打开”；
4. 在确认框中再次点击“仍要打开”。

卸载：运行镜像中的“卸载 VoCoType-linux.command”。用户配置和模型默认保留。

$SIGNING_NOTE
TEXT
chmod +x "$DMG_ROOT/卸载 VoCoType-linux.command"
test -d "$DMG_ROOT/VoCoType-linux.app/Contents/Resources/InputMethod/VoCoType-linux.app"
test -L "$DMG_ROOT/Applications"
test -f "$DMG_ROOT/.background/dmg-background.png"
test ! -e "$DMG_ROOT/安装 VoCoType-linux.command"
test ! -e "$DMG_ROOT/VoCoType-linux 设置.app"

# Create a writable image first so Finder can persist the conventional icon
# layout and background into .DS_Store, then convert it to compressed UDZO.
rm -f "$DMG" "$RW_DMG"
rm -rf "$DMG_MOUNT"
mkdir -p "$DMG_MOUNT"
hdiutil create -volname "$VOLUME_NAME" -srcfolder "$DMG_ROOT" \
  -ov -format UDRW "$RW_DMG" >/dev/null
hdiutil attach "$RW_DMG" -readwrite -noverify -noautoopen \
  -mountpoint "$DMG_MOUNT" >/dev/null
cleanup_dmg_mount() {
  hdiutil detach "$DMG_MOUNT" >/dev/null 2>&1 || true
}
trap cleanup_dmg_mount EXIT

# First let Finder create and flush its default .DS_Store record. On a brand-new
# image, writing custom icon sizes before this initialization can be overwritten
# asynchronously by Finder's default 48/12 view settings.
open "$DMG_MOUNT"
sleep 1
osascript <<APPLESCRIPT
 set targetFolder to POSIX file "$DMG_MOUNT" as alias
 tell application "Finder"
   set targetDisk to disk of targetFolder
   tell targetDisk
     open
     update without registering applications
     delay 1
     close container window
     delay 2
   end tell
 end tell
APPLESCRIPT

# Reopen and persist the complete release layout.
open "$DMG_MOUNT"
sleep 1
osascript <<APPLESCRIPT
 set targetFolder to POSIX file "$DMG_MOUNT" as alias
 tell application "Finder"
   set targetDisk to disk of targetFolder
   tell targetDisk
     open
     set current view of container window to icon view
     set toolbar visible of container window to false
     set statusbar visible of container window to false
     set pathbar visible of container window to false
     set bounds of container window to {100, 100, 780, 520}
     set viewOptions to icon view options of container window
     set arrangement of viewOptions to not arranged
     set icon size of viewOptions to 96
     set text size of viewOptions to 13
     set shows icon preview of viewOptions to false
     set background picture of viewOptions to file ".background:dmg-background.png"
     set position of item "VoCoType-linux.app" of container window to {175, 215}
     set position of item "Applications" of container window to {505, 215}
     set position of item "使用说明.txt" of container window to {250, 355}
     set position of item "卸载 VoCoType-linux.command" of container window to {430, 355}
     update without registering applications
     delay 2
     close container window
     delay 2
   end tell
 end tell
APPLESCRIPT
sync
hdiutil detach "$DMG_MOUNT" >/dev/null
trap - EXIT
hdiutil convert "$RW_DMG" -format UDZO -imagekey zlib-level=9 \
  -o "$DMG" >/dev/null
rm -f "$RW_DMG"

if [[ "$SIGNING_KIND" != adhoc-test ]]; then
  codesign --force --sign "$IDENTITY" --timestamp "$DMG"
  if [[ -n ${NOTARY_PROFILE:-} ]]; then
    xcrun notarytool submit "$DMG" \
      --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "$DMG"
    xcrun stapler validate "$DMG"
  fi
fi

shasum -a 256 "$DMG"
echo "$DMG"

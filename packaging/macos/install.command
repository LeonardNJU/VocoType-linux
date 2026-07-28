#!/bin/bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
SOURCE_INPUT="$SCRIPT_DIR/VoCoType-linux.app"
SOURCE_SETTINGS="$SCRIPT_DIR/VoCoType-linux 设置.app"
INPUT_DESTINATION="$HOME/Library/Input Methods/VoCoType-linux.app"
SETTINGS_DESTINATION="$HOME/Applications/VoCoType-linux 设置.app"
IDENTIFIER="io.github.LeonardNJU.VoCoTypeLinux.InputMethod"
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister

if [[ ! -d "$SOURCE_INPUT" || ! -d "$SOURCE_SETTINGS" ]]; then
  echo "安装镜像缺少 VoCoType-linux 输入法或设置 App。" >&2
  exit 1
fi

mkdir -p "$HOME/Library/Input Methods" "$HOME/Applications"
pkill -f "$INPUT_DESTINATION/Contents/MacOS/VoCoTypeLinuxInputMethod" 2>/dev/null || true
pkill -f "$SETTINGS_DESTINATION/Contents/MacOS/VoCoTypeLinuxSettings" 2>/dev/null || true
for runtime in vocotype-audio-recorder vocotype-core vocotype-streaming-worker vocotype-offline-worker; do
  pkill -f "$INPUT_DESTINATION/Contents/Resources/bin/$runtime" 2>/dev/null || true
done
rm -f "/tmp/vocotype-$(id -u).sock" "/tmp/vocotype-$(id -u).sock.lock"

for old in "$INPUT_DESTINATION" "$SETTINGS_DESTINATION"; do
  if [[ -d "$old" ]]; then
    "$LSREGISTER" -u "$old" >/dev/null 2>&1 || true
  fi
done
rm -rf "$INPUT_DESTINATION" "$SETTINGS_DESTINATION"
ditto "$SOURCE_INPUT" "$INPUT_DESTINATION"
ditto "$SOURCE_SETTINGS" "$SETTINGS_DESTINATION"
"$LSREGISTER" -f -R "$INPUT_DESTINATION" >/dev/null
"$LSREGISTER" -f -R "$SETTINGS_DESTINATION" >/dev/null

if [[ -f "$SCRIPT_DIR/.adhoc-test" ]]; then
  xattr -dr com.apple.quarantine "$INPUT_DESTINATION" 2>/dev/null || true
  xattr -dr com.apple.quarantine "$SETTINGS_DESTINATION" 2>/dev/null || true
fi

TOOL="$INPUT_DESTINATION/Contents/Resources/bin/vocotype-input-source-tool"
"$TOOL" --install "$INPUT_DESTINATION" "$IDENTIFIER"
open "$SETTINGS_DESTINATION" --args --settings-page 1 2>/dev/null || true

cat <<'MESSAGE'

VoCoType-linux 已安装并激活：
  ~/Library/Input Methods/VoCoType-linux.app
  ~/Applications/VoCoType-linux 设置.app

“VoCoType-linux 设置”现在是真正的独立 App，可由 Raycast、Spotlight 或 Finder 启动，并独立申请麦克风权限。

无需切换输入法：
  F9       语音输入
  Shift+F9 长句润色
  Ctrl+F9  语音编辑

当前系统拼音、双拼、鼠须管或其他键盘输入法不会被替换。
首次录音时请允许麦克风权限。
MESSAGE

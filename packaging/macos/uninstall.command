#!/bin/bash
set -euo pipefail

INPUT_DESTINATION="$HOME/Library/Input Methods/VoCoType-linux.app"
IDENTIFIER="io.github.LeonardNJU.VoCoTypeLinux.InputMethod"
TOOL="$INPUT_DESTINATION/Contents/Resources/bin/vocotype-input-source-tool"
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister
FRONTENDS=(
  "/Applications/VoCoType-linux.app"
  "$HOME/Applications/VoCoType-linux.app"
  "$HOME/Applications/VoCoType-linux 设置.app"
)

if [[ -x "$TOOL" ]]; then
  "$TOOL" --disable "$IDENTIFIER" || true
fi
pkill -f "$INPUT_DESTINATION/Contents/MacOS/VoCoTypeLinuxInputMethod" 2>/dev/null || true
for frontend in "${FRONTENDS[@]}"; do
  pkill -f "$frontend/Contents/MacOS/VoCoTypeLinuxSettings" 2>/dev/null || true
done
for runtime in vocotype-audio-recorder vocotype-core vocotype-streaming-worker vocotype-offline-worker; do
  pkill -f "$INPUT_DESTINATION/Contents/Resources/bin/$runtime" 2>/dev/null || true
done
rm -f "/tmp/vocotype-$(id -u).sock" "/tmp/vocotype-$(id -u).sock.lock"

if [[ -d "$INPUT_DESTINATION" ]]; then
  "$LSREGISTER" -u "$INPUT_DESTINATION" >/dev/null 2>&1 || true
fi
for frontend in "${FRONTENDS[@]}"; do
  if [[ -d "$frontend" ]]; then
    "$LSREGISTER" -u "$frontend" >/dev/null 2>&1 || true
  fi
done
rm -rf "$INPUT_DESTINATION"
for frontend in "${FRONTENDS[@]}"; do
  rm -rf "$frontend"
done
killall TextInputMenuAgent 2>/dev/null || true

echo "VoCoType-linux 应用与输入法组件已卸载。用户配置和模型仍保留在 Library 目录中。"

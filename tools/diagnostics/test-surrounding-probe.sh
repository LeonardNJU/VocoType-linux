#!/usr/bin/env bash
set -euo pipefail

LOG_FILE="${VOCOTYPE_LOG_FILE:-$HOME/.local/share/vocotype/ibus.log}"

echo "VoCoType Surrounding Text Probe"
echo
echo "使用方法："
echo "1) 切换到 VoCoType 输入法"
echo "2) 在任意输入框放置光标（可先输入几句文本）"
echo "3) 按 Ctrl+Shift+F9"
echo "4) 当前输入框会插入一段 [VT-SURR ...] 调试文本"
echo
echo "日志过滤：$LOG_FILE"
echo "按 Ctrl+C 退出"
echo

mkdir -p "$(dirname "$LOG_FILE")"
touch "$LOG_FILE"

tail -f "$LOG_FILE" | rg --line-buffered "SURROUNDING_PROBE|Client capabilities updated|Key event:"

#!/bin/bash
# VoCoType IBus 卸载脚本

set -e

INSTALL_DIR="$HOME/.local/share/vocotype"
COMPONENT_DIR="$HOME/.local/share/ibus/component"
LIBEXEC_DIR="$HOME/.local/libexec"
SYSTEM_COMPONENT="/usr/share/ibus/component/vocotype.xml"
VOCOTYPE_CONFIG_DIR="$HOME/.config/vocotype"

FCITX_PRESENT=0
if [ -d "$HOME/.local/share/vocotype-fcitx5" ] || \
   [ -f "$HOME/.local/share/fcitx5/addon/vocotype.conf" ] || \
   [ -f "$HOME/.local/lib/fcitx5/vocotype.so" ] || \
   [ -f "$HOME/.local/lib64/fcitx5/vocotype.so" ]; then
    FCITX_PRESENT=1
fi

echo "=== VoCoType IBus 卸载 ==="
echo ""

# 检查是否已安装
if [ ! -d "$INSTALL_DIR" ] && [ ! -f "$LIBEXEC_DIR/ibus-engine-vocotype" ]; then
    echo "未检测到 VoCoType 安装"
    exit 0
fi

echo "请选择卸载级别："
echo "  [1] 快速卸载（保留 .venv 和模型，方便下次安装）"
echo "  [2] 完全卸载（删除所有内容）"
echo ""
read -r -p "请输入选项 (默认 1): " UNINSTALL_LEVEL

KEEP_VENV=1
case "$UNINSTALL_LEVEL" in
    2)
        KEEP_VENV=0
        echo ""
        echo "将完全删除以下内容："
        [ -d "$INSTALL_DIR" ] && echo "  - $INSTALL_DIR"
        ;;
    ""|1|*)
        KEEP_VENV=1
        echo ""
        echo "将删除以下内容（保留 .venv 和模型）："
        [ -d "$INSTALL_DIR/app" ] && echo "  - $INSTALL_DIR/app"
        [ -d "$INSTALL_DIR/ibus" ] && echo "  - $INSTALL_DIR/ibus"
        [ -d "$INSTALL_DIR/settings_center" ] && echo "  - $INSTALL_DIR/settings_center"
        [ -f "$INSTALL_DIR/vocotype_version.py" ] && echo "  - $INSTALL_DIR/vocotype_version.py"
        ;;
esac

[ -f "$COMPONENT_DIR/vocotype.xml" ] && echo "  - $COMPONENT_DIR/vocotype.xml"
[ -f "$LIBEXEC_DIR/ibus-engine-vocotype" ] && echo "  - $LIBEXEC_DIR/ibus-engine-vocotype"
[ -f "$SYSTEM_COMPONENT" ] && echo "  - $SYSTEM_COMPONENT (需要 sudo)"
if [ "$FCITX_PRESENT" = "0" ]; then
    [ -f "$HOME/.local/bin/vocotype-settings" ] && echo "  - $HOME/.local/bin/vocotype-settings"
    [ -f "$HOME/.local/share/applications/io.github.LeonardNJU.VoCoType.Settings.desktop" ] && \
        echo "  - VoCoType 设置中心桌面入口"
fi
echo "  - 保留用户配置和术语库: $VOCOTYPE_CONFIG_DIR"
echo ""

read -r -p "确认卸载？(y/N): " CONFIRM
if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
    echo "已取消"
    exit 0
fi

echo ""
echo "正在卸载..."

# 删除文件
if [ "$KEEP_VENV" = "1" ]; then
    # 快速卸载：删除已安装代码，保留 .venv 和模型
    rm -rf "$INSTALL_DIR/app"
    rm -rf "$INSTALL_DIR/ibus"
    rm -rf "$INSTALL_DIR/settings_center"
    rm -f "$INSTALL_DIR/vocotype_version.py"
else
    # 完全卸载：删除整个安装目录
    rm -rf "$INSTALL_DIR"
fi

rm -f "$COMPONENT_DIR/vocotype.xml"
rm -f "$LIBEXEC_DIR/ibus-engine-vocotype"

# Runtime settings, terms, hotwords, and audio selection are user data. Keep
# them across uninstall; they may also be shared with the Fcitx integration.
if [ "$FCITX_PRESENT" = "0" ]; then
    rm -f "$HOME/.local/bin/vocotype-settings"
    rm -f "$HOME/.local/share/applications/io.github.LeonardNJU.VoCoType.Settings.desktop"
    rm -f "$HOME/.local/share/icons/hicolor/192x192/apps/vocotype.png"
fi

# 删除系统级组件文件
if [ -f "$SYSTEM_COMPONENT" ]; then
    if sudo rm -f "$SYSTEM_COMPONENT"; then
        echo "✓ 已删除系统组件文件"
    else
        echo "⚠️  无法删除系统组件文件: $SYSTEM_COMPONENT"
        echo "   请手动执行: sudo rm -f $SYSTEM_COMPONENT"
    fi
fi

echo ""
echo "=== 卸载完成 ==="
echo ""
echo "请执行以下步骤完成清理："
echo ""
echo "1. 从输入法列表中移除 VoCoType:"
echo "   设置 → 键盘 → 输入源 → 选择 VoCoType → 点击 '-' 删除"
echo ""
echo "2. 重启 IBus:"
echo "   ibus restart"
echo ""
echo "用户配置已保留在 $VOCOTYPE_CONFIG_DIR。确需删除时请手动确认后执行："
echo "   rm -rf $VOCOTYPE_CONFIG_DIR"
echo ""

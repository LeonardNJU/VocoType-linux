#!/bin/bash
# VocoType 纯净卸载脚本
# 只删除 VocoType 相关文件，不影响其他软件

# set -e

echo "=== VocoType 纯净卸载 ==="
echo ""

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 计数器
REMOVED_COUNT=0

# 删除文件/目录的函数（如果存在）
remove_if_exists() {
    local path="$1"
    local desc="$2"
    if [ -e "$path" ]; then
        rm -rf "$path"
        echo -e "${GREEN}✓${NC} 已删除: $desc"
        ((REMOVED_COUNT++))
        return 0
    fi
    return 1
}

echo "【第一步】停止相关服务..."

# 1. 停止 systemd 用户服务（如果存在）
if systemctl --user is-active vocotype-fcitx5-backend.service >/dev/null 2>&1; then
    systemctl --user stop vocotype-fcitx5-backend.service 2>/dev/null || true
    systemctl --user disable vocotype-fcitx5-backend.service 2>/dev/null || true
    echo -e "${GREEN}✓${NC} 已停止 vocotype-fcitx5-backend 服务"
fi

# 2. 停止可能运行的进程
if pgrep -f "ibus-engine-vocotype" >/dev/null 2>&1; then
    pkill -f "ibus-engine-vocotype" 2>/dev/null || true
    echo -e "${GREEN}✓${NC} 已停止 ibus-engine-vocotype 进程"
fi

if pgrep -f "fcitx5_server.py" >/dev/null 2>&1; then
    pkill -f "fcitx5_server.py" 2>/dev/null || true
    echo -e "${GREEN}✓${NC} 已停止 fcitx5_server.py 进程"
fi

echo ""
echo "【第二步】删除用户级文件..."

# IBus 版本
remove_if_exists "$HOME/.local/share/vocotype" "IBus 版本安装目录"
remove_if_exists "$HOME/.local/libexec/ibus-engine-vocotype" "IBus 引擎启动脚本"
remove_if_exists "$HOME/.local/share/ibus/component/vocotype.xml" "IBus 组件配置（用户级）"

# Fcitx5 版本
remove_if_exists "$HOME/.local/share/vocotype-fcitx5" "Fcitx5 版本安装目录"
remove_if_exists "$HOME/.local/share/fcitx5/addon/vocotype.conf" "Fcitx5 addon 配置"
remove_if_exists "$HOME/.local/share/fcitx5/inputmethod/vocotype.conf" "Fcitx5 输入法配置"
remove_if_exists "$HOME/.local/lib64/fcitx5/vocotype.so" "Fcitx5 库文件 (lib64)"
remove_if_exists "$HOME/.local/lib64/fcitx5/libvocotype.so" "Fcitx5 库符号链接 (lib64)"
remove_if_exists "$HOME/.local/lib/fcitx5/vocotype.so" "Fcitx5 库文件 (lib)"
remove_if_exists "$HOME/.local/lib/fcitx5/libvocotype.so" "Fcitx5 库符号链接 (lib)"
remove_if_exists "$HOME/.local/bin/vocotype-fcitx5-backend" "Fcitx5 后端启动脚本"

# 配置和缓存
remove_if_exists "$HOME/.config/vocotype" "配置文件目录"
remove_if_exists "$HOME/.cache/vocotype" "缓存/模型文件目录"

# systemd 服务
remove_if_exists "$HOME/.config/systemd/user/vocotype-fcitx5-backend.service" "systemd 用户服务"

# 环境变量配置
remove_if_exists "$HOME/.config/environment.d/fcitx5-vocotype.conf" "环境变量配置"

echo ""
echo "【第三步】检查系统级文件..."

# 检查系统级 IBus 组件
if [ -f "/usr/share/ibus/component/vocotype.xml" ]; then
    echo -e "${YELLOW}⚠${NC} 发现系统级文件需要删除:"
    echo "   /usr/share/ibus/component/vocotype.xml"
    echo ""
    read -p "是否删除此系统级文件？(需要 sudo 权限) [y/N]: " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        if sudo rm -f /usr/share/ibus/component/vocotype.xml; then
            echo -e "${GREEN}✓${NC} 已删除系统级 IBus 组件"
            ((REMOVED_COUNT++))
        else
            echo -e "${RED}✗${NC} 删除失败，请手动执行:"
            echo "   sudo rm /usr/share/ibus/component/vocotype.xml"
        fi
    else
        echo "跳过系统级文件删除。如需手动删除，请执行:"
        echo "   sudo rm /usr/share/ibus/component/vocotype.xml"
    fi
fi

echo ""
echo "【第四步】清理 IBus/Fcitx5 缓存..."

# 重启输入法框架以清除缓存
if command -v ibus >/dev/null 2>&1; then
    echo "重启 IBus..."
    ibus restart 2>/dev/null || true
fi

if command -v fcitx5 >/dev/null 2>&1; then
    echo "Fcitx5 需要重新启动才能生效"
    echo "   请执行: fcitx5 -r"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ $REMOVED_COUNT -eq 0 ]; then
    echo -e "${GREEN}✓${NC} 未发现 VocoType 安装文件"
    echo "   系统已经是纯净状态"
else
    echo -e "${GREEN}✓${NC} 已清理 $REMOVED_COUNT 个 VocoType 相关文件/目录"
    echo "   系统已恢复纯净状态"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "提示:"
echo "  - 如需重新安装，请重新运行安装脚本"
echo "  - IBus 用户可能需要重新添加输入法"
echo "  - Fcitx5 用户需要执行: fcitx5 -r"
echo ""

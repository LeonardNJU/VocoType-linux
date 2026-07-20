#!/bin/bash
# VocoType Fcitx5 休眠后失效问题修复脚本
#
# 问题：休眠后服务未自动恢复
# 原因：Restart=on-failure 只在异常退出时重启，休眠时系统正常终止进程不会触发重启
# 解决：改为 Restart=always

set -e

SERVICE_FILE="$HOME/.config/systemd/user/vocotype-fcitx5-backend.service"
BACKUP_FILE="$SERVICE_FILE.backup-$(date +%Y%m%d-%H%M%S)"

echo "========================================"
echo " VocoType Fcitx5 休眠问题修复工具"
echo "========================================"
echo ""
echo "此脚本将修复休眠后输入法失效的问题"
echo ""

# 检查服务文件是否存在
if [ ! -f "$SERVICE_FILE" ]; then
    echo "错误: 服务文件不存在: $SERVICE_FILE"
    echo ""
    echo "请先运行安装脚本:"
    echo "  bash fcitx5/scripts/install.sh"
    exit 1
fi

echo "[1/4] 备份当前服务文件..."
cp "$SERVICE_FILE" "$BACKUP_FILE"
echo "✓ 备份保存到: $BACKUP_FILE"
echo ""

echo "[2/4] 更新服务配置..."
cat > "$SERVICE_FILE" << 'EOF'
[Unit]
Description=VoCoType Fcitx5 Backend Service
After=graphical-session.target

[Service]
Type=simple
ExecStart=%h/.local/bin/vocotype-fcitx5-backend
# 改进重启策略：任何情况下都重启（包括休眠后被终止）
Restart=always
RestartSec=5s
Environment="PYTHONIOENCODING=UTF-8"

[Install]
WantedBy=default.target
EOF
echo "✓ 服务配置已更新"
echo ""

echo "[3/4] 重新加载 systemd 配置..."
systemctl --user daemon-reload
echo "✓ systemd 配置已重新加载"
echo ""

echo "[4/4] 重启服务..."
systemctl --user restart vocotype-fcitx5-backend.service
echo "✓ 服务已重启"
echo ""

# 验证服务状态
echo "========================================"
echo " 验证修复结果"
echo "========================================"
echo ""

if systemctl --user is-active vocotype-fcitx5-backend.service &>/dev/null; then
    echo "✅ 服务运行正常"
else
    echo "❌ 服务未运行，请检查日志:"
    echo "   journalctl --user -u vocotype-fcitx5-backend.service -n 20"
    exit 1
fi

if [ -S "/tmp/vocotype-fcitx5.sock" ]; then
    echo "✅ Socket 文件已创建"
else
    echo "⚠️  Socket 文件尚未创建，请等待几秒后检查"
fi

echo ""
echo "========================================"
echo " 修复完成"
echo "========================================"
echo ""
echo "更改内容:"
echo "  Restart=on-failure → Restart=always"
echo ""
echo "效果:"
echo "  - 休眠后服务会自动重启"
echo "  - 任何意外终止都会自动恢复"
echo ""
echo "测试方法:"
echo "  1. 重启 Fcitx5: fcitx5 -r"
echo "  2. 测试输入法是否正常"
echo "  3. 进入休眠/待机，唤醒后测试"
echo ""
echo "如果需要恢复旧配置:"
echo "  cp $BACKUP_FILE $SERVICE_FILE"
echo "  systemctl --user daemon-reload"
echo "  systemctl --user restart vocotype-fcitx5-backend.service"
echo ""

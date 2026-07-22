#!/bin/bash
# VocoType Fcitx5 Backend 诊断脚本
# 用于排查休眠后输入法失效问题

echo "========================================"
echo " VocoType Fcitx5 Backend 诊断工具"
echo "========================================"
echo ""

SOCKET_PATH="/tmp/vocotype-fcitx5.sock"
SERVICE_NAME="vocotype-fcitx5-backend.service"
BACKEND_SCRIPT="$HOME/.local/share/vocotype-fcitx5/backend/fcitx5_server.py"
VENV_PYTHON="$HOME/.local/share/vocotype-fcitx5/.venv/bin/python"

# 1. 检查 socket 文件是否存在
echo "[1/6] 检查 socket 文件..."
if [ -S "$SOCKET_PATH" ]; then
    echo "✓ Socket 文件存在: $SOCKET_PATH"
    ls -lh "$SOCKET_PATH"
else
    echo "✗ Socket 文件不存在: $SOCKET_PATH"
fi
echo ""

# 2. 检查后端进程是否运行
echo "[2/6] 检查后端进程..."
if pgrep -f "fcitx5_server.py" > /dev/null; then
    echo "✓ 后端进程正在运行:"
    ps aux | grep -v grep | grep "fcitx5_server.py"
else
    echo "✗ 后端进程未运行"
fi
echo ""

# 3. 检查 systemd 服务状态
echo "[3/6] 检查 systemd 服务状态..."
if systemctl --user is-active "$SERVICE_NAME" &>/dev/null; then
    echo "✓ 服务状态: 运行中"
else
    echo "✗ 服务状态: 未运行"
fi

if systemctl --user is-enabled "$SERVICE_NAME" &>/dev/null; then
    echo "✓ 服务已启用（开机自启）"
else
    echo "✗ 服务未启用"
fi

echo ""
echo "详细服务状态:"
systemctl --user status "$SERVICE_NAME" --no-pager | head -20
echo ""

# 4. 检查最近的服务日志
echo "[4/6] 检查最近的服务日志..."
echo "最近 10 条日志:"
journalctl --user -u "$SERVICE_NAME" -n 10 --no-pager
echo ""

# 5. 测试 socket 连接
echo "[5/6] 测试 socket 连接..."
if [ -S "$SOCKET_PATH" ]; then
    if command -v nc &>/dev/null; then
        echo "发送 ping 请求到后端..."
        RESPONSE=$(echo '{"type":"ping"}' | nc -U -w 2 "$SOCKET_PATH" 2>&1)
        if [ $? -eq 0 ]; then
            echo "✓ Socket 连接成功"
            echo "响应: $RESPONSE"
        else
            echo "✗ Socket 连接失败"
            echo "错误: $RESPONSE"
        fi
    else
        echo "⚠ nc (netcat) 未安装，跳过连接测试"
        echo "安装: sudo pacman -S gnu-netcat (Arch) 或 sudo apt install netcat (Debian/Ubuntu)"
    fi
else
    echo "⚠ Socket 文件不存在，跳过连接测试"
fi
echo ""

# 6. 检查 Python 依赖
echo "[6/6] 检查 Python 环境..."
if [ -f "$VENV_PYTHON" ]; then
    echo "✓ Python 虚拟环境存在"
    "$VENV_PYTHON" --version

    echo ""
    echo "检查关键依赖:"
    "$VENV_PYTHON" -c "from app.funasr_server import FunASRServer; print('✓ FunASRServer')" 2>&1 || echo "✗ FunASRServer 缺失"
else
    echo "✗ Python 虚拟环境不存在: $VENV_PYTHON"
fi
echo ""

# 总结和建议
echo "========================================"
echo " 诊断总结"
echo "========================================"
echo ""

if [ ! -S "$SOCKET_PATH" ]; then
    echo "问题: Socket 文件丢失"
    echo ""
    echo "可能原因:"
    echo "  1. 后端服务未启动"
    echo "  2. 服务启动失败"
    echo "  3. /tmp 目录被清理（休眠/重启后）"
    echo ""
    echo "建议操作:"
    echo "  1. 重启服务:"
    echo "     systemctl --user restart $SERVICE_NAME"
    echo ""
    echo "  2. 查看详细日志:"
    echo "     journalctl --user -u $SERVICE_NAME -f"
    echo ""
    echo "  3. 启用服务自动启动:"
    echo "     systemctl --user enable $SERVICE_NAME"
    echo ""
elif ! pgrep -f "fcitx5_server.py" > /dev/null; then
    echo "问题: Socket 文件存在但进程未运行（僵尸 socket）"
    echo ""
    echo "建议操作:"
    echo "  1. 删除陈旧的 socket 文件:"
    echo "     rm -f $SOCKET_PATH"
    echo ""
    echo "  2. 重启服务:"
    echo "     systemctl --user restart $SERVICE_NAME"
    echo ""
else
    echo "服务看起来正常运行"
    echo ""
    echo "如果仍然有连接问题，尝试:"
    echo "  1. 完全重启服务:"
    echo "     systemctl --user restart $SERVICE_NAME"
    echo ""
    echo "  2. 重启 Fcitx5:"
    echo "     fcitx5 -r"
    echo ""
fi

echo "========================================"
echo ""
echo "完整服务日志:"
echo "  journalctl --user -u $SERVICE_NAME -n 50"
echo ""
echo "实时监控日志:"
echo "  journalctl --user -u $SERVICE_NAME -f"
echo ""

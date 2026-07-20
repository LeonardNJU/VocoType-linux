#!/bin/bash
# 实时调试vocotype按键处理

LOG_FILE="/tmp/vocotype-debug-$(date +%Y%m%d-%H%M%S).log"

echo "=== VoCoType 实时调试 ==="
echo "日志文件: $LOG_FILE"
echo ""
echo "步骤："
echo "1. 重启IBus并启用vocotype调试日志"
echo "2. 切换到vocotype输入法"
echo "3. 尝试打字，观察日志输出"
echo "4. 按Ctrl+C停止"
echo ""
read -p "按Enter开始..."

# 停止当前IBus
echo "停止IBus..."
ibus exit

# 等待IBus完全停止
sleep 2

# 设置环境变量并启动IBus（带vocotype调试日志）
echo "启动IBus（调试模式）..."
VOCOTYPE_LOG_FILE="$LOG_FILE" ibus-daemon -drx &

# 等待IBus启动
sleep 3

echo ""
echo "IBus已启动，日志记录到: $LOG_FILE"
echo ""
echo "现在请："
echo "1. 切换到vocotype输入法"
echo "2. 在任意文本框中尝试打字"
echo "3. 观察下面的日志输出"
echo ""
echo "=== 实时日志 ==="
echo ""

# 实时显示日志
tail -f "$LOG_FILE"

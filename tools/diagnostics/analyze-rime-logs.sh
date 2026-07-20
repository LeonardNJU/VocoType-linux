#!/bin/bash
# VoCoType Rime日志分析工具
# 用于快速筛选和分析vocotype IBus引擎的日志

LOG_FILE="${VOCOTYPE_LOG_FILE:-$HOME/.local/share/vocotype/ibus.log}"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 检查日志文件是否存在
check_log_file() {
    if [ ! -f "$LOG_FILE" ]; then
        echo -e "${RED}错误: 日志文件不存在: $LOG_FILE${NC}"
        echo "请确保vocotype已运行并生成日志"
        exit 1
    fi
}

# 显示菜单
show_menu() {
    echo ""
    echo -e "${BLUE}=== VoCoType Rime 日志分析工具 ===${NC}"
    echo -e "日志文件: ${GREEN}$LOG_FILE${NC}"
    echo ""
    echo "1) 查看session生命周期"
    echo "2) 查看错误日志"
    echo "3) 查看按键处理日志"
    echo "4) 查看编码问题"
    echo "5) Session统计"
    echo "6) 实时监控"
    echo "7) 查看完整日志"
    echo "0) 退出"
    echo ""
}

# 1. Session生命周期查看器
view_sessions() {
    echo -e "${YELLOW}=== Session生命周期 ===${NC}"
    grep --color=always -E "Session ID:.*created|session.*released|session.*destroyed|active sessions:" "$LOG_FILE" | less -R
}

# 2. 错误日志查看器
view_errors() {
    echo -e "${YELLOW}=== 错误日志 ===${NC}"
    grep --color=always -iE "ERROR|Failed|失败|错误|WARNING" "$LOG_FILE" | less -R
}

# 3. 按键处理查看器
view_key_processing() {
    echo -e "${YELLOW}=== 按键处理日志 ===${NC}"
    grep --color=always -E "Key event:|Rime process_key:|handled=" "$LOG_FILE" | less -R
}

# 4. 编码问题查看器
view_encoding() {
    echo -e "${YELLOW}=== 编码问题 ===${NC}"
    grep --color=always -iE "decode|utf-8|gbk|UnicodeDecodeError|schema:" "$LOG_FILE" | less -R
}

# 5. Session统计
session_stats() {
    echo -e "${YELLOW}=== Session统计 ===${NC}"

    local created=$(grep -c "Session ID:.*created" "$LOG_FILE" 2>/dev/null || echo "0")
    local released=$(grep -c "released on disable" "$LOG_FILE" 2>/dev/null || echo "0")
    local destroyed=$(grep -c "session.*destroyed" "$LOG_FILE" 2>/dev/null || echo "0")
    local last_count=$(grep "active sessions:" "$LOG_FILE" 2>/dev/null | tail -1 | grep -oP 'active sessions: \K\d+' || echo "0")

    echo -e "总共创建的session: ${GREEN}$created${NC}"
    echo -e "总共释放的session (disable): ${GREEN}$released${NC}"
    echo -e "总共销毁的session (destroy): ${GREEN}$destroyed${NC}"
    echo -e "当前活跃session数量: ${GREEN}$last_count${NC}"
    echo ""

    if [ "$created" -gt 0 ]; then
        if [ "$last_count" -eq 0 ]; then
            echo -e "${GREEN}✓ 所有session已正确释放${NC}"
        elif [ "$last_count" -gt 3 ]; then
            echo -e "${RED}⚠ 警告: 活跃session数量较多，可能存在泄漏${NC}"
        else
            echo -e "${YELLOW}ℹ 当前有 $last_count 个活跃session（正常）${NC}"
        fi
    fi

    echo ""
    read -p "按Enter继续..."
}

# 6. 实时监控
monitor_live() {
    echo -e "${YELLOW}=== 实时监控模式 ===${NC}"
    echo "监控: Session、Rime、错误日志"
    echo -e "${RED}按Ctrl+C停止${NC}"
    echo ""
    tail -f "$LOG_FILE" | grep --line-buffered --color=always -E "Session|Rime|handled=|ERROR|Failed|active sessions:"
}

# 7. 查看完整日志
view_full_log() {
    echo -e "${YELLOW}=== 完整日志 ===${NC}"
    less "$LOG_FILE"
}

# 主循环
main() {
    check_log_file

    while true; do
        show_menu
        read -p "选择选项 [0-7]: " choice

        case $choice in
            1) view_sessions ;;
            2) view_errors ;;
            3) view_key_processing ;;
            4) view_encoding ;;
            5) session_stats ;;
            6) monitor_live ;;
            7) view_full_log ;;
            0)
                echo "退出"
                exit 0
                ;;
            *)
                echo -e "${RED}无效选项，请重试${NC}"
                sleep 1
                ;;
        esac
    done
}

# 运行主程序
main

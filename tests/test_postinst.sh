#!/bin/bash
# VoCoType postinst 脚本测试套件 v2
# 测试安装脚本的各种功能，包括输入法检测、模型下载提示等

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[1;34m'
NC='\033[0m'

# 测试计数器
TESTS_PASSED=0
TESTS_FAILED=0
TESTS_TOTAL=0

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
POSTINST_PATH="$PROJECT_ROOT/debian/postinst"

# 辅助函数
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_pass() { echo -e "${GREEN}[PASS]${NC} $1"; TESTS_PASSED=$((TESTS_PASSED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1)); }
log_fail() { echo -e "${RED}[FAIL]${NC} $1"; TESTS_FAILED=$((TESTS_FAILED + 1)); TESTS_TOTAL=$((TESTS_TOTAL + 1)); }

# 测试：脚本存在性
test_script_exists() {
    log_info "测试：postinst 脚本存在"
    if [ -f "$POSTINST_PATH" ]; then
        log_pass "postinst 脚本存在"
    else
        log_fail "postinst 脚本不存在: $POSTINST_PATH"
    fi
}

# 测试：脚本可执行
test_script_executable() {
    log_info "测试：postinst 脚本可执行"
    if [ -x "$POSTINST_PATH" ]; then
        log_pass "postinst 脚本是可执行的"
    else
        log_fail "postinst 脚本不可执行"
    fi
}

# 测试：脚本语法
test_script_syntax() {
    log_info "测试：postinst 脚本语法"
    if bash -n "$POSTINST_PATH" 2>/dev/null; then
        log_pass "postinst 脚本语法正确"
    else
        log_fail "postinst 脚本有语法错误"
    fi
}

# 测试：必要的函数定义
test_function_definitions() {
    log_info "测试：必要的函数定义"

    local required_functions=(
        "is_interactive"
        "has_display"
        "has_zenity"
        "has_kdialog"
        "has_gui_tool"
        "detect_im_framework"
        "is_fcitx5_user"
        "is_ibus_user"
        "models_exist"
        "get_installed_model_count"
        "prompt_download_zenity"
        "prompt_download_kdialog"
        "prompt_download_cli"
        "download_models"
        "setup_fcitx5_service"
        "setup_ibus"
        "reload_fcitx5"
        "is_first_install"
        "do_configure"
        "log_info"
        "log_success"
        "log_warn"
        "log_error"
    )

    for func in "${required_functions[@]}"; do
        if grep -q "^${func}()" "$POSTINST_PATH" 2>/dev/null || \
           grep -q "function ${func}" "$POSTINST_PATH" 2>/dev/null; then
            log_pass "函数 $func 已定义"
        else
            log_fail "函数 $func 未定义"
        fi
    done
}

# 测试：case 语句结构
test_case_structure() {
    log_info "测试：case 语句结构"

    if grep -q 'case "$1" in' "$POSTINST_PATH"; then
        log_pass "包含 case \"\$1\" in 结构"
    else
        log_fail "缺少 case \"\$1\" in 结构"
    fi

    if grep -q 'configure)' "$POSTINST_PATH"; then
        log_pass "包含 configure 处理"
    else
        log_fail "缺少 configure 处理"
    fi

    if grep -q 'abort-upgrade|abort-remove|abort-deconfigure' "$POSTINST_PATH"; then
        log_pass "包含 abort 处理"
    else
        log_fail "缺少 abort 处理"
    fi
}

# 测试：错误处理
test_error_handling() {
    log_info "测试：错误处理"

    if grep -q 'set -e' "$POSTINST_PATH"; then
        log_pass "启用了 set -e (错误时退出)"
    else
        log_fail "未启用 set -e"
    fi
}

# 测试：DEBHELPER 标记
test_debhelper_marker() {
    log_info "测试：DEBHELPER 标记"

    if grep -q '#DEBHELPER#' "$POSTINST_PATH"; then
        log_pass "包含 #DEBHELPER# 标记"
    else
        log_fail "缺少 #DEBHELPER# 标记"
    fi
}

# 测试：输入法框架检测
test_im_framework_detection() {
    log_info "测试：输入法框架检测"

    # 检查 detect_im_framework 函数
    if grep -q 'detect_im_framework()' "$POSTINST_PATH"; then
        log_pass "detect_im_framework 函数存在"
    else
        log_fail "缺少 detect_im_framework 函数"
    fi

    # 检查 Fcitx5 检测
    if grep -q 'fcitx5' "$POSTINST_PATH"; then
        log_pass "包含 Fcitx5 检测"
    else
        log_fail "缺少 Fcitx5 检测"
    fi

    # 检查 IBus 检测
    if grep -q 'ibus' "$POSTINST_PATH"; then
        log_pass "包含 IBus 检测"
    else
        log_fail "缺少 IBus 检测"
    fi

    # 检查 is_fcitx5_user
    if grep -q 'is_fcitx5_user()' "$POSTINST_PATH"; then
        log_pass "is_fcitx5_user 函数存在"
    else
        log_fail "缺少 is_fcitx5_user 函数"
    fi

    # 检查 is_ibus_user
    if grep -q 'is_ibus_user()' "$POSTINST_PATH"; then
        log_pass "is_ibus_user 函数存在"
    else
        log_fail "缺少 is_ibus_user 函数"
    fi
}

# 测试：首次安装检测
test_first_install_detection() {
    log_info "测试：首次安装检测"

    if grep -q 'is_first_install()' "$POSTINST_PATH"; then
        log_pass "is_first_install 函数存在"
    else
        log_fail "缺少 is_first_install 函数"
    fi

    # 检查是否检查 $2 参数
    if grep -q '\$2' "$POSTINST_PATH"; then
        log_pass "检查 $2 参数（上一次版本）"
    else
        log_fail "未检查 $2 参数"
    fi
}

# 测试：模型检测
test_model_detection() {
    log_info "测试：模型检测功能"

    if grep -q 'models_exist()' "$POSTINST_PATH"; then
        log_pass "models_exist 函数存在"
    else
        log_fail "缺少 models_exist 函数"
    fi

    if grep -q 'get_installed_model_count()' "$POSTINST_PATH"; then
        log_pass "get_installed_model_count 函数存在"
    else
        log_fail "缺少 get_installed_model_count 函数"
    fi

    # 检查模型路径
    local expected_models=(
        "speech_paraformer-large"
        "speech_fsmn_vad"
        "punc_ct-transformer"
    )

    for model in "${expected_models[@]}"; do
        if grep -q "$model" "$POSTINST_PATH"; then
            log_pass "包含模型: $model"
        else
            log_fail "缺少模型: $model"
        fi
    done
}

# 测试：GUI 提示支持
test_gui_support() {
    log_info "测试：GUI 提示支持"

    if grep -q 'prompt_download_zenity()' "$POSTINST_PATH"; then
        log_pass "zenity 提示函数存在"
    else
        log_fail "缺少 zenity 提示函数"
    fi

    if grep -q 'prompt_download_kdialog()' "$POSTINST_PATH"; then
        log_pass "kdialog 提示函数存在"
    else
        log_fail "缺少 kdialog 提示函数"
    fi

    if grep -q 'has_zenity()' "$POSTINST_PATH"; then
        log_pass "has_zenity 检测函数存在"
    else
        log_fail "缺少 has_zenity 检测函数"
    fi

    if grep -q 'has_kdialog()' "$POSTINST_PATH"; then
        log_pass "has_kdialog 检测函数存在"
    else
        log_fail "缺少 has_kdialog 检测函数"
    fi

    if grep -q 'has_gui_tool()' "$POSTINST_PATH"; then
        log_pass "has_gui_tool 检测函数存在"
    else
        log_fail "缺少 has_gui_tool 检测函数"
    fi
}

# 测试：CLI 提示支持
test_cli_support() {
    log_info "测试：CLI 提示支持"

    if grep -q 'prompt_download_cli()' "$POSTINST_PATH"; then
        log_pass "CLI 提示函数存在"
    else
        log_fail "缺少 CLI 提示函数"
    fi
}

# 测试：模型下载功能
test_model_download() {
    log_info "测试：模型下载功能"

    if grep -q 'download_models()' "$POSTINST_PATH"; then
        log_pass "download_models 函数存在"
    else
        log_fail "缺少 download_models 函数"
    fi

    if grep -q 'vocotype-download-models' "$POSTINST_PATH"; then
        log_pass "引用了下载脚本"
    else
        log_fail "未引用下载脚本"
    fi
}

# 测试：Fcitx5 服务配置
test_fcitx5_service_config() {
    log_info "测试：Fcitx5 服务配置"

    if grep -q 'setup_fcitx5_service()' "$POSTINST_PATH"; then
        log_pass "setup_fcitx5_service 函数存在"
    else
        log_fail "缺少 setup_fcitx5_service 函数"
    fi

    if grep -q 'vocotype-fcitx5-backend.service' "$POSTINST_PATH"; then
        log_pass "引用了 systemd 服务文件"
    else
        log_fail "未引用 systemd 服务文件"
    fi

    if grep -q 'systemctl --user' "$POSTINST_PATH"; then
        log_pass "使用 systemctl --user"
    else
        log_fail "未使用 systemctl --user"
    fi
}

# 测试：IBus 配置
test_ibus_config() {
    log_info "测试：IBus 配置"

    if grep -q 'setup_ibus()' "$POSTINST_PATH"; then
        log_pass "setup_ibus 函数存在"
    else
        log_fail "缺少 setup_ibus 函数"
    fi
}

# 测试：环境检测
test_environment_detection() {
    log_info "测试：环境检测"

    if grep -q 'is_interactive()' "$POSTINST_PATH"; then
        log_pass "包含交互式环境检测"
    else
        log_fail "缺少交互式环境检测"
    fi

    if grep -q 'has_display()' "$POSTINST_PATH"; then
        log_pass "包含图形界面检测"
    else
        log_fail "缺少图形界面检测"
    fi
}

# 测试：sudo 处理
test_sudo_handling() {
    log_info "测试：sudo 处理"

    if grep -q 'SUDO_USER' "$POSTINST_PATH"; then
        log_pass "处理 SUDO_USER 环境变量"
    else
        log_fail "未处理 SUDO_USER 环境变量"
    fi

    if grep -q 'getent passwd' "$POSTINST_PATH"; then
        log_pass "使用 getent 获取用户信息"
    else
        log_fail "未使用 getent 获取用户信息"
    fi
}

# 测试：日志函数
test_logging_functions() {
    log_info "测试：日志函数"

    local log_funcs=("log_info" "log_success" "log_warn" "log_error")
    for func in "${log_funcs[@]}"; do
        if grep -q "^${func}()" "$POSTINST_PATH"; then
            log_pass "日志函数 $func 已定义"
        else
            log_fail "缺少日志函数 $func"
        fi
    done
}

# 测试：版本信息
test_version_info() {
    log_info "测试：版本信息"

    if grep -q 'VERSION=' "$POSTINST_PATH"; then
        log_pass "定义了 VERSION 变量"
    else
        log_fail "未定义 VERSION 变量"
    fi
}

# 测试：常量定义
test_constants() {
    log_info "测试：常量定义"

    if grep -q 'CACHE_DIR=' "$POSTINST_PATH"; then
        log_pass "定义了 CACHE_DIR"
    else
        log_fail "未定义 CACHE_DIR"
    fi

    if grep -q 'MODEL_NAMES=' "$POSTINST_PATH"; then
        log_pass "定义了 MODEL_NAMES 数组"
    else
        log_fail "未定义 MODEL_NAMES 数组"
    fi
}

# 测试：do_configure 主函数
test_do_configure() {
    log_info "测试：do_configure 主函数"

    if grep -q 'do_configure()' "$POSTINST_PATH"; then
        log_pass "do_configure 函数存在"
    else
        log_fail "缺少 do_configure 函数"
    fi
}

# 测试：边界情况处理
test_edge_cases() {
    log_info "测试：边界情况处理"

    # 检查是否处理空框架的情况（使用 -z 检查）
    if grep -q '\-z "$framework"' "$POSTINST_PATH" || \
       grep -q '\-z "$im_framework"' "$POSTINST_PATH" || \
       grep -q 'im_framework=""' "$POSTINST_PATH" || \
       grep -q "im_framework=''" "$POSTINST_PATH"; then
        log_pass "处理空输入法框架的情况"
    else
        log_fail "未处理空输入法框架的情况"
    fi

    # 检查是否处理模型不存在的情况
    if grep -q 'models_exist' "$POSTINST_PATH"; then
        log_pass "检查模型存在性"
    else
        log_fail "未检查模型存在性"
    fi

    # 检查是否处理升级情况
    if grep -q '升级安装' "$POSTINST_PATH" || \
       grep -q 'upgrade' "$POSTINST_PATH"; then
        log_pass "处理升级情况"
    else
        log_fail "未处理升级情况"
    fi

    # 检查是否处理无法检测交互式环境的情况
    if grep -q '无法检测交互式环境' "$POSTINST_PATH"; then
        log_pass "处理无法检测交互式环境的情况"
    else
        log_fail "未处理无法检测交互式环境的情况"
    fi
}

# 测试：下载包装脚本存在性
test_download_wrapper_exists() {
    log_info "测试：下载包装脚本存在"

    local wrapper_path="$PROJECT_ROOT/scripts/vocotype-download-models"
    if [ -f "$wrapper_path" ]; then
        log_pass "下载包装脚本存在"

        if [ -x "$wrapper_path" ]; then
            log_pass "下载包装脚本是可执行的"
        else
            log_fail "下载包装脚本不可执行"
        fi

        # 测试语法
        if python3 -m py_compile "$wrapper_path" 2>/dev/null; then
            log_pass "下载包装脚本语法正确"
        else
            log_fail "下载包装脚本有语法错误"
        fi
    else
        log_fail "下载包装脚本不存在"
    fi
}

# 测试 rules 文件
test_rules_file() {
    log_info "测试：debian/rules 文件"

    local rules_path="$PROJECT_ROOT/debian/rules"
    if [ -f "$rules_path" ]; then
        log_pass "rules 文件存在"

        if grep -q 'vocotype-download-models' "$rules_path"; then
            log_pass "rules 文件包含下载脚本安装"
        else
            log_fail "rules 文件缺少下载脚本安装"
        fi
    else
        log_fail "rules 文件不存在"
    fi
}

# 测试 install 文件
test_install_file() {
    log_info "测试：debian/install 文件"

    local install_path="$PROJECT_ROOT/debian/vocotype.install"
    if [ -f "$install_path" ]; then
        log_pass "install 文件存在"

        if grep -q 'vocotype-download-models' "$install_path"; then
            log_pass "install 文件包含下载脚本"
        else
            log_fail "install 文件缺少下载脚本"
        fi
    else
        log_fail "install 文件不存在"
    fi
}

# 打印测试摘要
print_summary() {
    echo ""
    echo "=========================================="
    echo "测试摘要"
    echo "=========================================="
    echo -e "通过: ${GREEN}$TESTS_PASSED${NC}"
    echo -e "失败: ${RED}$TESTS_FAILED${NC}"
    echo "总计: $TESTS_TOTAL"
    echo ""

    if [ $TESTS_FAILED -eq 0 ]; then
        echo -e "${GREEN}所有测试通过！${NC}"
        return 0
    else
        echo -e "${RED}有测试失败，请检查！${NC}"
        return 1
    fi
}

# 主函数
main() {
    echo "=========================================="
    echo "VoCoType Postinst 脚本测试 v2"
    echo "=========================================="
    echo ""

    # 检查 postinst 文件
    if [ ! -f "$POSTINST_PATH" ]; then
        echo "错误: 找不到 postinst 文件: $POSTINST_PATH"
        exit 1
    fi

    # 运行所有测试
    test_script_exists
    test_script_executable
    test_script_syntax
    test_function_definitions
    test_case_structure
    test_error_handling
    test_debhelper_marker
    test_im_framework_detection
    test_first_install_detection
    test_model_detection
    test_gui_support
    test_cli_support
    test_model_download
    test_fcitx5_service_config
    test_ibus_config
    test_environment_detection
    test_sudo_handling
    test_logging_functions
    test_version_info
    test_constants
    test_do_configure
    test_edge_cases
    test_download_wrapper_exists
    test_rules_file
    test_install_file

    # 打印摘要
    print_summary
}

# 运行主函数
main "$@"

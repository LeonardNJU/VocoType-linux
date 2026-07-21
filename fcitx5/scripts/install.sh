#!/bin/bash
# VoCoType Fcitx 5 安装脚本
#
# 用法: install.sh [--device <id>] [--sample-rate <rate>] [--skip-audio]
#                            [--non-interactive] [--python-choice user|project|system]
#                            [--slm-provider preserve|disabled|remote|local]
#                            [--install-system-deps] [--bootstrap-uv]
#   --device <id>      指定音频设备ID，跳过交互式配置
#   --sample-rate <rate>  指定采样率（默认44100）
#   --skip-audio       跳过音频配置
#   --non-interactive  供图形设置中心调用，不读取终端输入
#   --preserve-config  保留已有 SLM/运行配置
#   --python-choice    非交互环境选择；默认 user
#   --slm-provider     非交互 SLM 选择；默认 preserve
#   --install-system-deps  缺依赖时通过 pkexec 弹出桌面授权并自动安装
#   --bootstrap-uv     缺少兼容 Python 时在用户目录安装 uv/Python 3.12
#
# 历史问题修复记录：
# 1. 旧版 FCITX_ADDON_DIRS 覆盖会遮蔽系统 addon，必须清理
# 2. 库文件前缀 - 需要创建 libvocotype.so 符号链接
# 3. inputmethod 配置 - 文件扩展名应为 .conf（不是 .conf.in）
# 4. listInputMethods() - C++ 代码必须实现此方法才能被 Fcitx5 发现
# 5. C++20 标准 - Fcitx5 日志宏需要 source_location

set -e

# 解析命令行参数
SKIP_AUDIO=false
AUDIO_DEVICE=""
SAMPLE_RATE="44100"
NON_INTERACTIVE=false
PRESERVE_CONFIG=false
PY_CHOICE_OVERRIDE="user"
SLM_PROVIDER_OVERRIDE="preserve"
INSTALL_SYSTEM_DEPS=false
BOOTSTRAP_UV=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-audio)
            SKIP_AUDIO=true
            shift
            ;;
        --device)
            AUDIO_DEVICE="$2"
            shift 2
            ;;
        --sample-rate)
            SAMPLE_RATE="$2"
            shift 2
            ;;
        --non-interactive)
            NON_INTERACTIVE=true
            shift
            ;;
        --preserve-config)
            PRESERVE_CONFIG=true
            shift
            ;;
        --python-choice)
            PY_CHOICE_OVERRIDE="$2"
            shift 2
            ;;
        --slm-provider)
            SLM_PROVIDER_OVERRIDE="$2"
            shift 2
            ;;
        --install-system-deps)
            INSTALL_SYSTEM_DEPS=true
            shift
            ;;
        --bootstrap-uv)
            BOOTSTRAP_UV=true
            shift
            ;;
        *)
            shift
            ;;
    esac
done

PROJECT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../.." && pwd)"
source "$PROJECT_DIR/installers/runtime-common.sh"
INSTALL_DIR="$HOME/.local/share/vocotype-fcitx5"
INSTALLER_DIR="$PROJECT_DIR/installers"
INSTALLED_SETUP_AUDIO_SCRIPT="$INSTALL_DIR/installers/setup-audio.py"
PYTHON_MIN_MINOR=11
PYTHON_MAX_MINOR=12
DEFAULT_UV_PYTHON="3.12"
SYSTEM_DEPS_HELPER="$PROJECT_DIR/installers/install-system-dependencies.sh"
SYSTEM_FCITX_HELPER="$PROJECT_DIR/installers/manage-fcitx-system-integration.sh"
VOCOTYPE_VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$PROJECT_DIR/vocotype_version.py")
REUSE_SYSTEM_MODULE=false
if [ -f "$PROJECT_DIR/.system-package" ] || [ -f /usr/share/vocotype/.system-package ]; then
    REUSE_SYSTEM_MODULE=true
fi
SYSTEM_FCITX_SOURCE_MARKER=/usr/share/vocotype/.source-fcitx-integration

source_system_fcitx_matches_build() {
    local built_module="$PROJECT_DIR/fcitx5/module/build/vocotype.so"
    local built_addon="$PROJECT_DIR/fcitx5/data/vocotype.conf"
    local installed_module
    [ -f "$SYSTEM_FCITX_SOURCE_MARKER" ] || return 1
    [ -f /usr/share/fcitx5/addon/vocotype.conf ] || return 1
    cmp -s "$built_addon" /usr/share/fcitx5/addon/vocotype.conf || return 1
    for installed_module in \
        /usr/lib/fcitx5/vocotype.so \
        /usr/lib64/fcitx5/vocotype.so \
        /usr/lib/*/fcitx5/vocotype.so; do
        if [ -f "$installed_module" ] && cmp -s "$built_module" "$installed_module"; then
            return 0
        fi
    done
    return 1
}

# SLM 可选配置（默认关闭）
ENABLE_SLM=0
SLM_PROVIDER="local_ephemeral"
SLM_ENDPOINT="http://127.0.0.1:18080/v1/chat/completions"
SLM_MODEL="Qwen/Qwen3.5-0.8B"
SLM_LOCAL_MODEL="$SLM_MODEL"
SLM_LOCAL_PYTHON=""
SLM_TIMEOUT_MS=12000
SLM_WARMUP_TIMEOUT_MS=90000
SLM_MIN_CHARS=8
SLM_MAX_TOKENS=96
SLM_ENABLE_THINKING=0
SLM_API_KEY=""
SLM_INSTALL_LOCAL_DEPS=0

resolve_python_cmd() {
    local py="$1"

    if [[ "$py" == "~/"* ]]; then
        py="$HOME/${py#~/}"
    fi

    if [[ "$py" == */* ]]; then
        [ -x "$py" ] || return 1
        echo "$py"
        return 0
    fi

    command -v "$py" 2>/dev/null || return 1
}

escape_sed_replacement() {
    local value="$1"
    value=${value//\\/\\\\}
    value=${value//&/\\&}
    printf '%s' "$value"
}

is_supported_python() {
    local py="$1"
    local py_version
    local major
    local minor

    py_version=$(get_python_version "$py") || return 1
    major=$(echo "$py_version" | cut -d. -f1)
    minor=$(echo "$py_version" | cut -d. -f2)
    [ "$major" -eq 3 ] && [ "$minor" -ge "$PYTHON_MIN_MINOR" ] && [ "$minor" -le "$PYTHON_MAX_MINOR" ]
}

detect_system_python() {
    local py
    local resolved_py

    for py in python3.12 python3.11 python3; do
        if resolved_py=$(resolve_python_cmd "$py"); then
            if is_supported_python "$resolved_py"; then
                echo "$resolved_py"
                return 0
            fi
        fi
    done
    return 1
}

bootstrap_uv() {
    command -v uv >/dev/null 2>&1 && return 0
    [ "$BOOTSTRAP_UV" = true ] || return 1
    echo "正在用户目录安装 uv，以便自动准备 Python 3.12…"
    mkdir -p "$HOME/.local/bin"
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh
    else
        echo "错误: 无法自动安装 uv，系统缺少 curl/wget。" >&2
        return 1
    fi
    export PATH="$HOME/.local/bin:$PATH"
    command -v uv >/dev/null 2>&1
}

install_system_fcitx_integration() {
    local module="$PROJECT_DIR/fcitx5/module/build/vocotype.so"
    local addon="$PROJECT_DIR/fcitx5/data/vocotype.conf"
    if [ ! -x "$SYSTEM_FCITX_HELPER" ]; then
        echo "错误: 系统 Fcitx integration 辅助程序不存在: $SYSTEM_FCITX_HELPER" >&2
        return 1
    fi
    if source_system_fcitx_matches_build; then
        echo "✓ 源码安装器管理的系统 addon 已与当前构建一致，无需再次授权"
        return 0
    fi
    if [ "$NON_INTERACTIVE" = true ]; then
        if ! command -v pkexec >/dev/null 2>&1; then
            echo "错误: 源码安装 VoCoType（Fcitx 5）需要 Polkit 授权写入系统 addon 目录。" >&2
            return 1
        fi
        echo "AUTH_REQUIRED: 即将弹出管理员授权窗口以安装 VoCoType（Fcitx 5）系统 addon。"
        pkexec --disable-internal-agent "$(command -v bash)" "$SYSTEM_FCITX_HELPER" install "$module" "$addon" "$VOCOTYPE_VERSION"
        return
    fi
    if command -v pkexec >/dev/null 2>&1 && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
        pkexec --disable-internal-agent "$(command -v bash)" "$SYSTEM_FCITX_HELPER" install "$module" "$addon" "$VOCOTYPE_VERSION"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$(command -v bash)" "$SYSTEM_FCITX_HELPER" install "$module" "$addon" "$VOCOTYPE_VERSION"
    else
        echo "错误: 需要 pkexec 或 sudo 安装系统级 VoCoType（Fcitx 5）addon。" >&2
        return 1
    fi
}

install_system_dependencies() {
    if [ "$INSTALL_SYSTEM_DEPS" != true ]; then
        return 1
    fi
    if ! command -v pkexec >/dev/null 2>&1; then
        echo "错误: 未检测到 pkexec，无法显示管理员授权窗口。" >&2
        echo "请安装 polkit 后重试，或先手动安装系统依赖。" >&2
        return 1
    fi
    if [ ! -r "$SYSTEM_DEPS_HELPER" ]; then
        echo "错误: 系统依赖辅助程序不存在: $SYSTEM_DEPS_HELPER" >&2
        return 1
    fi
    echo "AUTH_REQUIRED: 即将弹出管理员授权窗口以安装 VoCoType（Fcitx 5）所需的系统依赖。"
    pkexec --disable-internal-agent "$(command -v bash)" "$SYSTEM_DEPS_HELPER" fcitx5
}

print_python_help() {
    echo ""
    echo "原因: VoCoType 使用 onnxruntime 运行语音识别模型，"
    echo "      而 onnxruntime 官方尚未支持 Python 3.13+。"
    echo "      参考: https://github.com/microsoft/onnxruntime/issues/21292"
    echo ""
    echo "解决方案："
    echo ""
    echo "  【推荐】安装 uv（自动管理 Python 版本和虚拟环境）："
    echo "    curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo "    然后重新打开终端，再运行本脚本"
    echo ""
    echo "  或手动安装 Python 3.12："
    echo "    Fedora: sudo dnf install python3.12"
    echo "    Ubuntu: sudo apt install python3.12"
    echo "    Arch:   sudo pacman -S python312"
    echo ""
    echo "  或使用 conda 创建兼容环境（安装脚本可手动指定解释器）："
    echo "    conda create -n vocotype python=3.12"
    echo "    conda activate vocotype"
}

echo "=== VoCoType Fcitx 5 语音输入法安装 ==="
emit_install_progress 2 "准备安装 VoCoType（Fcitx 5）"
echo "项目目录: $PROJECT_DIR"
echo ""

if [ "$NON_INTERACTIVE" = true ]; then
    case "$SLM_PROVIDER_OVERRIDE" in
        preserve)
            echo "非交互安装：保留已有 SLM 配置"
            ;;
        disabled)
            ENABLE_SLM=0
            ;;
        remote)
            ENABLE_SLM=1
            SLM_PROVIDER="remote"
            SLM_TIMEOUT_MS=20000
            ;;
        local|local_ephemeral)
            ENABLE_SLM=1
            SLM_PROVIDER="local_ephemeral"
            SLM_INSTALL_LOCAL_DEPS=1
            ;;
        *)
            echo "错误: 未知 --slm-provider: $SLM_PROVIDER_OVERRIDE"
            exit 1
            ;;
    esac
else
    echo "是否启用长句 SLM 润色（Shift+F9）？"
    echo "  [1] 不启用（默认）- 不安装 SLM 模型，保持最低资源占用"
    echo "  [2] 启用 - 配置 SLM 润色"
    echo ""
    read -r -p "请输入选项 (默认 1): " SLM_CHOICE
    case "$SLM_CHOICE" in
    2)
        ENABLE_SLM=1
        echo ""
        echo "您选择启用 SLM 润色。"
        echo "请选择 SLM 运行方式："
        echo "  [1] 本地一次性加载（推荐）：按下 Shift+F9 预加载，润色后释放"
        echo "  [2] 远程 HTTP 服务：调用已有 endpoint（OpenAI 兼容）"
        read -r -p "请输入选项 (默认 1): " SLM_PROVIDER_CHOICE

        if [ "$SLM_PROVIDER_CHOICE" = "2" ]; then
            SLM_PROVIDER="remote"
            SLM_TIMEOUT_MS=20000
            SLM_WARMUP_TIMEOUT_MS=12000
            SLM_MAX_TOKENS=128
            read -r -p "SLM 模型名 (默认 $SLM_MODEL): " SLM_MODEL_INPUT
            if [ -n "$SLM_MODEL_INPUT" ]; then
                SLM_MODEL="$SLM_MODEL_INPUT"
            fi

            read -r -p "SLM Endpoint (默认 $SLM_ENDPOINT): " SLM_ENDPOINT_INPUT
            if [ -n "$SLM_ENDPOINT_INPUT" ]; then
                SLM_ENDPOINT="$SLM_ENDPOINT_INPUT"
            fi
            read -r -s -p "SLM API Key（可留空，输入时不回显）: " SLM_API_KEY_INPUT
            echo ""
            if [ -n "$SLM_API_KEY_INPUT" ]; then
                SLM_API_KEY="$SLM_API_KEY_INPUT"
            fi
        else
            SLM_PROVIDER="local_ephemeral"
            SLM_TIMEOUT_MS=12000
            SLM_WARMUP_TIMEOUT_MS=90000
            SLM_MAX_TOKENS=96
            SLM_ENABLE_THINKING=0
            SLM_API_KEY=""
            read -r -p "本地模型名/路径 (默认 $SLM_LOCAL_MODEL): " SLM_LOCAL_MODEL_INPUT
            if [ -n "$SLM_LOCAL_MODEL_INPUT" ]; then
                SLM_LOCAL_MODEL="$SLM_LOCAL_MODEL_INPUT"
                SLM_MODEL="$SLM_LOCAL_MODEL_INPUT"
            fi
            read -r -p "是否安装本地 SLM 依赖（torch/transformers/sentencepiece/socksio）? (Y/n): " INSTALL_SLM_DEPS
            if [[ ! "$INSTALL_SLM_DEPS" =~ ^[Nn]$ ]]; then
                SLM_INSTALL_LOCAL_DEPS=1
            fi
        fi
        ;;
    ""|1|*)
        ENABLE_SLM=0
        SLM_API_KEY=""
        echo ""
        echo "已禁用 SLM 润色（Shift+F9 不会触发润色）。"
        ;;
    esac
fi
echo ""

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. 检查 Fcitx 5
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
emit_install_progress 5 "检查 Fcitx 5 与系统依赖"
echo "[1/8] 检查 Fcitx 5..."
if ! command -v fcitx5 &>/dev/null; then
    echo "未检测到 Fcitx 5。"
    install_system_dependencies || {
        echo "错误: Fcitx 5 尚未安装，且自动安装未完成。" >&2
        exit 1
    }
fi
if ! command -v fcitx5 &>/dev/null; then
    echo "错误: 系统依赖安装完成后仍未检测到 fcitx5。" >&2
    exit 1
fi
echo "✓ Fcitx 5 已安装"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2-5. Fcitx module preparation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if [ "$REUSE_SYSTEM_MODULE" = true ]; then
    echo ""
    emit_install_progress 15 "检查原生软件包提供的 Fcitx module"
    echo "[2/8] 原生软件包已提供 Fcitx 5 module"
    echo "✓ 跳过开发依赖检查"
    echo ""
    echo "[3/8] 复用系统 Fcitx 5 全局 Module"
    echo "✓ 无需重新编译"
    echo ""
    echo "[4/8] 系统 Module 与 addon 元数据已安装"
    echo "✓ 保留系统包管理的文件"
    echo ""
    echo "[5/8] 使用系统标准 addon 路径"
    rm -f "$HOME/.config/environment.d/fcitx5-vocotype.conf"
    unset FCITX_ADDON_DIRS || true
    echo "✓ 已清理旧版用户 FCITX_ADDON_DIRS 覆盖"
else
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. 检查编译依赖
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo ""
emit_install_progress 15 "检查 C++ module 编译依赖"
echo "[2/8] 检查编译依赖..."
missing_deps=()

# 检查 CMake
if ! command -v cmake &>/dev/null; then
    missing_deps+=("cmake")
fi

# 检查 pkg-config
if ! command -v pkg-config &>/dev/null; then
    missing_deps+=("pkg-config")
fi

# 检查 Fcitx 5 开发库（多种检测方式）
fcitx5_found=false
for pkg in Fcitx5Core fcitx5-core Fcitx5Module fcitx5; do
    if pkg-config --exists "$pkg" 2>/dev/null; then
        fcitx5_found=true
        break
    fi
done

if [ "$fcitx5_found" = false ]; then
    for include_dir in /usr/include /usr/local/include; do
        if [ -f "$include_dir/Fcitx5/Core/fcitx/addoninstance.h" ] || \
           [ -f "$include_dir/fcitx5/core/addoninstance.h" ]; then
            fcitx5_found=true
            break
        fi
    done
fi

if [ "$fcitx5_found" = false ]; then
    missing_deps+=("fcitx5-devel (或 libfcitx5-dev)")
fi

# 检查 nlohmann-json
json_found=false
for pkg in nlohmann_json json; do
    if pkg-config --exists "$pkg" 2>/dev/null; then
        json_found=true
        break
    fi
done

if [ "$json_found" = false ]; then
    for include_dir in /usr/include /usr/local/include; do
        if [ -f "$include_dir/nlohmann/json.hpp" ]; then
            json_found=true
            break
        fi
    done
fi

if [ "$json_found" = false ]; then
    missing_deps+=("nlohmann-json-devel (或 nlohmann-json3-dev)")
fi

if [ ${#missing_deps[@]} -gt 0 ]; then
    echo "缺少以下编译依赖:"
    for dep in "${missing_deps[@]}"; do
        echo "  - $dep"
    done
    install_system_dependencies || {
        echo "错误: 自动安装系统依赖未完成。" >&2
        exit 1
    }
    restart_args=(
        --non-interactive
        --skip-audio
        --python-choice "$PY_CHOICE_OVERRIDE"
        --slm-provider "$SLM_PROVIDER_OVERRIDE"
    )
    [ "$PRESERVE_CONFIG" = true ] && restart_args+=(--preserve-config)
    [ "$INSTALL_SYSTEM_DEPS" = true ] && restart_args+=(--install-system-deps)
    [ "$BOOTSTRAP_UV" = true ] && restart_args+=(--bootstrap-uv)
    exec bash "$0" "${restart_args[@]}"
fi
echo "✓ 编译依赖已满足"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. 编译 C++ 全局 Module
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo ""
emit_install_progress 25 "编译 VoCoType Fcitx module"
echo "[3/8] 编译 C++ 全局 Module..."
mkdir -p "$PROJECT_DIR/fcitx5/module/build"
cd "$PROJECT_DIR/fcitx5/module/build"

cmake .. -DCMAKE_INSTALL_PREFIX="$HOME/.local"
make -j$(nproc)
echo "✓ 编译成功"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. 安装系统级 C++ 全局 Module
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo ""
emit_install_progress 35 "安装系统 VoCoType（Fcitx 5）addon"
echo "[4/8] 安装 VoCoType（Fcitx 5）系统 addon..."
install_system_fcitx_integration || {
    echo "错误: 系统级 VoCoType（Fcitx 5）addon 安装失败。" >&2
    exit 1
}
# 旧版用户级 module 不会被所有 Fcitx 构建扫描，并可能误导状态检查。
rm -f \
    "$HOME/.local/lib/fcitx5/vocotype.so" \
    "$HOME/.local/lib/fcitx5/libvocotype.so" \
    "$HOME/.local/lib64/fcitx5/vocotype.so" \
    "$HOME/.local/lib64/fcitx5/libvocotype.so" \
    "$HOME/.local/share/fcitx5/addon/vocotype.conf"
rm -f "$HOME/.local/share/fcitx5/inputmethod/vocotype.conf"
echo "✓ 系统级 VoCoType（Fcitx 5）addon 已安装"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. 清理旧版 addon 路径覆盖
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo ""
emit_install_progress 45 "清理旧版 Fcitx 路径覆盖"
echo "[5/8] 清理旧版 Fcitx addon 路径覆盖..."
rm -f "$HOME/.config/environment.d/fcitx5-vocotype.conf"
unset FCITX_ADDON_DIRS || true
echo "✓ 使用 Fcitx 5 标准 addon 搜索路径"

fi

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. 安装 Python 后端
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo ""
emit_install_progress 52 "安装 VoCoType Python 后端"
echo "[6/8] 安装 Python 后端..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR/scripts" "$INSTALL_DIR/installers"

# 复制文件
cp -r "$PROJECT_DIR/app" "$INSTALL_DIR/"
cp -r "$PROJECT_DIR/settings_center" "$INSTALL_DIR/"
cp -r "$PROJECT_DIR/fcitx5/backend" "$INSTALL_DIR/"
cp "$PROJECT_DIR/vocotype_version.py" "$INSTALL_DIR/"
cp "$INSTALLER_DIR/setup-audio.py" "$INSTALLED_SETUP_AUDIO_SCRIPT"

TERMS_DIR="$HOME/.config/vocotype"
TERMS_FILE="$TERMS_DIR/terms.yaml"
LEGACY_TERMS_FILE="$TERMS_DIR/user-dictionary.yaml"
mkdir -p "$TERMS_DIR"
if [ ! -e "$TERMS_FILE" ] && [ ! -e "$LEGACY_TERMS_FILE" ]; then
    cp "$PROJECT_DIR/data/terms.yaml" "$TERMS_FILE"
    echo "✓ 已创建统一术语库模板: $TERMS_FILE"
else
    echo "✓ 保留已有术语库配置"
fi

# 创建 __init__.py
touch "$INSTALL_DIR/backend/__init__.py"

echo "✓ Python 后端已安装"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 7. 配置 Python 环境
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo ""
emit_install_progress 60 "配置 Python 运行环境"
echo "[7/8] 配置 Python 环境..."

if [ "$NON_INTERACTIVE" = true ]; then
    case "$PY_CHOICE_OVERRIDE" in
        project) PY_CHOICE=1 ;;
        user|"") PY_CHOICE=2 ;;
        system) PY_CHOICE=3 ;;
        *)
            echo "错误: 未知 --python-choice: $PY_CHOICE_OVERRIDE"
            exit 1
            ;;
    esac
    echo "非交互安装：Python 环境 = $PY_CHOICE_OVERRIDE"
else
    echo "请选择 Python 环境："
    echo "  [1] 使用项目虚拟环境（开发用，依赖当前仓库）: $PROJECT_DIR/.venv"
    echo "  [2] 使用用户级虚拟环境（默认，删除工作区后仍可用）: $INSTALL_DIR/.venv"
    echo "  [3] 使用系统 Python（省空间，需自行安装依赖）"
    echo "  [4] 手动指定 Python 解释器（如 conda 环境）"
    read -r -p "请输入选项 (默认 2): " PY_CHOICE
fi

USE_SYSTEM_PYTHON=0
CUSTOM_PYTHON_CMD=""
case "$PY_CHOICE" in
    2)
        PYTHON="$INSTALL_DIR/.venv/bin/python"
        ;;
    3)
        USE_SYSTEM_PYTHON=1
        ;;
    4)
        read -r -e -p "请输入 Python 解释器路径或命令名: " CUSTOM_PYTHON_INPUT
        if [ -z "$CUSTOM_PYTHON_INPUT" ]; then
            echo "错误: 未输入 Python 解释器"
            exit 1
        fi

        CUSTOM_PYTHON_CMD=$(resolve_python_cmd "$CUSTOM_PYTHON_INPUT") || {
            echo "错误: 未找到 Python 解释器: $CUSTOM_PYTHON_INPUT"
            exit 1
        }

        if ! is_supported_python "$CUSTOM_PYTHON_CMD"; then
            custom_py_version=$(get_python_version "$CUSTOM_PYTHON_CMD")
            echo "错误: 解释器版本不兼容（当前 ${custom_py_version:-unknown}，需要 Python 3.11-3.12）"
            print_python_help
            exit 1
        fi

        # 选项 4：用手动指定解释器创建/驱动安装目录虚拟环境。
        PYTHON="$INSTALL_DIR/.venv/bin/python"
        ;;
    1)
        PYTHON="$PROJECT_DIR/.venv/bin/python"
        ;;
    ""|2|*)
        PYTHON="$INSTALL_DIR/.venv/bin/python"
        ;;
esac

# 检测可用的 Python 版本（需要 3.11-3.12，onnxruntime 不支持 3.13+）
PYTHON_CMD=""
if [ "$USE_SYSTEM_PYTHON" = "1" ]; then
    PYTHON_CMD=$(detect_system_python) || {
        echo "错误: 需要 Python 3.11-3.12"
        print_python_help
        exit 1
    }
    PYTHON="$PYTHON_CMD"
    echo "使用系统 Python: $PYTHON_CMD ($(get_python_version "$PYTHON_CMD"))"
else
    if [ -n "$CUSTOM_PYTHON_CMD" ]; then
        PYTHON_CMD="$CUSTOM_PYTHON_CMD"
        echo "使用手动指定的 Python: $PYTHON_CMD ($(get_python_version "$PYTHON_CMD"))"
    elif command -v uv &>/dev/null || bootstrap_uv; then
        PYTHON_CMD="$DEFAULT_UV_PYTHON"
        echo "使用 uv 管理 Python: $PYTHON_CMD"
    else
        PYTHON_CMD=$(detect_system_python) || {
            echo "错误: 需要 Python 3.11-3.12，且 uv 自动安装未启用或失败。"
            print_python_help
            exit 1
        }
        echo "检测到兼容的 Python: $PYTHON_CMD ($(get_python_version "$PYTHON_CMD"))"
    fi
fi

# 创建虚拟环境
if [ "$USE_SYSTEM_PYTHON" != "1" ] && [ ! -x "$PYTHON" ]; then
    VENV_DIR="$(dirname "$PYTHON")/.."
    if command -v uv &>/dev/null; then
        echo "使用 uv 创建虚拟环境: $VENV_DIR"
        uv venv --python "$PYTHON_CMD" "$VENV_DIR"
    else
        echo "使用 venv 创建虚拟环境: $VENV_DIR"
        "$PYTHON_CMD" -m venv "$VENV_DIR"
    fi
fi

if [ ! -x "$PYTHON" ]; then
    echo "错误: 未找到 Python 可执行文件: $PYTHON"
    exit 1
fi

# 安装依赖
if [ "$USE_SYSTEM_PYTHON" = "1" ]; then
    if ! "$PYTHON" - << 'PY' >/dev/null 2>&1
import jieba  # noqa: F401
import modelscope  # noqa: F401
import numpy  # noqa: F401
from scipy import signal  # noqa: F401
import yaml  # noqa: F401
from itn.chinese.inverse_normalizer import InverseNormalizer  # noqa: F401
import sounddevice  # noqa: F401
import soundfile  # noqa: F401
import funasr_onnx  # noqa: F401
PY
    then
        echo "系统 Python 缺少依赖。请先执行："
        echo "  $PYTHON -m pip install -r $PROJECT_DIR/requirements.txt"
        exit 1
    fi
else
    if command -v uv &>/dev/null; then
        echo "使用 uv 安装依赖..."
        uv pip install -r "$PROJECT_DIR/requirements.txt" --python "$PYTHON"
    else
        echo "使用 pip 安装依赖..."
        "$PYTHON" -m pip install --upgrade pip
        "$PYTHON" -m pip install -r "$PROJECT_DIR/requirements.txt"
    fi
fi

echo "✓ Python 环境已配置"

emit_install_progress 70 "下载并校验 ASR、VAD 与标点模型"
download_and_verify_asr_models "$PYTHON" "$INSTALL_DIR" || exit 1

FCITX5_BACKEND_CONFIG="$HOME/.config/vocotype/fcitx5-backend.json"
if [ "$PRESERVE_CONFIG" = true ] && [ -f "$FCITX5_BACKEND_CONFIG" ]; then
    preserved_slm=$(
        "$PYTHON" - "$FCITX5_BACKEND_CONFIG" << 'PY'
import json
import sys

try:
    with open(sys.argv[1], "r", encoding="utf-8") as handle:
        config = json.load(handle)
    slm = config.get("slm", {}) if isinstance(config, dict) else {}
    enabled = 1 if bool(slm.get("enabled", False)) else 0
    provider = str(slm.get("provider", "local_ephemeral"))
except Exception:
    enabled = 0
    provider = "local_ephemeral"
print(f"{enabled}	{provider}")
PY
    )
    IFS=$'	' read -r ENABLE_SLM SLM_PROVIDER <<< "$preserved_slm"
    if [ "$ENABLE_SLM" = "1" ] && [ "$SLM_PROVIDER" = "local_ephemeral" ]; then
        SLM_INSTALL_LOCAL_DEPS=1
        echo "检测到已有本地 SLM 配置，将验证/修复本地模型依赖"
    fi
fi

if [ "$ENABLE_SLM" = "1" ] && [ "$SLM_PROVIDER" = "local_ephemeral" ] && [ "$SLM_INSTALL_LOCAL_DEPS" = "1" ]; then
    echo ""
    echo "安装本地 SLM 依赖（torch/transformers/sentencepiece/socksio）..."
    if [ "$USE_SYSTEM_PYTHON" = "1" ]; then
        if ! "$PYTHON" -c "import torch, transformers, sentencepiece, socksio" >/dev/null 2>&1; then
            echo "⚠️  系统 Python 缺少本地 SLM 依赖，请手动安装："
            echo "  $PYTHON -m pip install torch transformers sentencepiece socksio"
            echo "   或改用虚拟环境重新安装。"
        fi
    elif command -v uv &>/dev/null; then
        uv pip install torch transformers sentencepiece socksio --python "$PYTHON"
    else
        "$PYTHON" -m pip install torch transformers sentencepiece socksio
    fi
fi

echo ""
echo "[可选] 写入 SLM 配置..."
if [ "$PRESERVE_CONFIG" = true ] && [ -f "$FCITX5_BACKEND_CONFIG" ]; then
    echo "✓ 已保留现有配置: $FCITX5_BACKEND_CONFIG"
else
    write_slm_config_json \
        "$FCITX5_BACKEND_CONFIG" \
        "$PYTHON" \
        "$ENABLE_SLM" \
        "$SLM_PROVIDER" \
        "$SLM_ENDPOINT" \
        "$SLM_MODEL" \
        "$SLM_LOCAL_MODEL" \
        "$SLM_LOCAL_PYTHON" \
        "$SLM_TIMEOUT_MS" \
        "$SLM_MIN_CHARS" \
        "$SLM_MAX_TOKENS" \
        "$SLM_WARMUP_TIMEOUT_MS" \
        "$SLM_ENABLE_THINKING" \
        "$SLM_API_KEY"
    echo "✓ 已写入配置: $FCITX5_BACKEND_CONFIG"
fi

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 8. 音频设备配置和 ASR 验收
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo ""
emit_install_progress 78 "保留或写入麦克风设备配置"
echo "[8/8] 音频设备配置..."

if [ -n "$AUDIO_DEVICE" ]; then
    # 使用命令行指定的设备，直接写入配置
    echo "使用指定的音频设备: $AUDIO_DEVICE (采样率: $SAMPLE_RATE)"
    mkdir -p "$HOME/.config/vocotype"
    cat > "$HOME/.config/vocotype/audio.conf" << EOF
[audio]
device_id = $AUDIO_DEVICE
sample_rate = $SAMPLE_RATE
EOF
    echo "✓ 音频配置已保存"
elif [ "$SKIP_AUDIO" = true ]; then
    # 图形安装只安装程序；设备选择、回放和真实转录集中在 Playground。
    echo "跳过命令行音频向导；可在设置中心 Playground 选择设备并试用。"
else
    # 交互式配置
    echo ""
    echo "现在需要配置您的麦克风设备。"
    echo "这个过程会："
    echo "  - 列出可用的音频输入设备"
    echo "  - 测试录音和播放"
    echo "  - 验证语音识别效果"
    echo ""

    if ! "$PYTHON" "$INSTALLED_SETUP_AUDIO_SCRIPT"; then
        echo ""
        echo "错误: 音频设备与识别验收未完成，安装不能标记为成功。" >&2
        echo "请重新运行安装器，或在设置中心完成麦克风测试。" >&2
        exit 1
    fi
fi

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 安装图形设置中心入口
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo ""
emit_install_progress 84 "安装图形设置中心"
echo "安装图形设置中心..."
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/192x192/apps"
PYTHON_SED=$(escape_sed_replacement "$PYTHON")
cat > "$HOME/.local/bin/vocotype-settings" << 'EOF'
#!/bin/bash
PREFERRED_INSTALL_DIR="VOCOTYPE_INSTALL_DIR"
PREFERRED_PYTHON="VOCOTYPE_PYTHON"
export VOCOTYPE_PROJECT_DIR="VOCOTYPE_PROJECT_DIR_VALUE"

run_settings() {
    local install_dir="$1"
    local python_bin="$2"
    shift 2
    [ -f "$install_dir/settings_center/application.py" ] || return 1
    if [[ "$python_bin" == */* ]]; then
        [ -x "$python_bin" ] || return 1
    else
        command -v "$python_bin" >/dev/null 2>&1 || return 1
    fi
    export PYTHONPATH="$install_dir${PYTHONPATH:+:$PYTHONPATH}"
    exec "$python_bin" -m settings_center.application "$@"
}

run_settings "$PREFERRED_INSTALL_DIR" "$PREFERRED_PYTHON" "$@"
run_settings "$HOME/.local/share/vocotype-fcitx5" "$HOME/.local/share/vocotype-fcitx5/.venv/bin/python" "$@"
run_settings "$HOME/.local/share/vocotype" "$HOME/.local/share/vocotype/.venv/bin/python" "$@"

echo "VoCoType 设置中心运行时不存在，请重新安装或修复。" >&2
exit 1
EOF
sed -i "s|VOCOTYPE_PYTHON|$PYTHON_SED|g" "$HOME/.local/bin/vocotype-settings"
INSTALL_DIR_SED=$(escape_sed_replacement "$INSTALL_DIR")
sed -i "s|VOCOTYPE_INSTALL_DIR|$INSTALL_DIR_SED|g" "$HOME/.local/bin/vocotype-settings"
PROJECT_DIR_SED=$(escape_sed_replacement "$PROJECT_DIR")
sed -i "s|VOCOTYPE_PROJECT_DIR_VALUE|$PROJECT_DIR_SED|g" "$HOME/.local/bin/vocotype-settings"
chmod +x "$HOME/.local/bin/vocotype-settings"
sed "s|Exec=vocotype-settings|Exec=$HOME/.local/bin/vocotype-settings|" \
    "$PROJECT_DIR/data/applications/io.github.LeonardNJU.VoCoType.Settings.desktop" > \
    "$HOME/.local/share/applications/io.github.LeonardNJU.VoCoType.Settings.desktop"
cp "$PROJECT_DIR/site/icon-192.png" \
   "$HOME/.local/share/icons/hicolor/192x192/apps/vocotype.png"
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
fi
echo "✓ 设置中心已安装，可运行: vocotype-settings"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 创建后台服务启动器
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo ""
emit_install_progress 88 "创建并启动后台服务"
echo "创建后台服务启动器..."
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/vocotype-fcitx5-recorder" << 'EOF'
#!/bin/bash
# VoCoType Fcitx5 录音启动器

PYTHON="VOCOTYPE_PYTHON"
RECORDER_SCRIPT="$HOME/.local/share/vocotype-fcitx5/backend/audio_recorder.py"

exec "$PYTHON" "$RECORDER_SCRIPT" "$@"
EOF
PYTHON_SED=$(escape_sed_replacement "$PYTHON")
sed -i "s|VOCOTYPE_PYTHON|$PYTHON_SED|g" "$HOME/.local/bin/vocotype-fcitx5-recorder"
chmod +x "$HOME/.local/bin/vocotype-fcitx5-recorder"

cat > "$HOME/.local/bin/vocotype-fcitx5-backend" << 'EOF'
#!/bin/bash
# VoCoType Fcitx5 Backend 服务

INSTALL_DIR="$HOME/.local/share/vocotype-fcitx5"
PYTHON="VOCOTYPE_PYTHON"
SERVER_SCRIPT="$INSTALL_DIR/backend/fcitx5_server.py"

# systemd 负责进程生命周期；重复实例会由 Unix socket 绑定失败明确报错。
exec "$PYTHON" "$SERVER_SCRIPT" "$@"
EOF
sed -i "s|VOCOTYPE_PYTHON|$PYTHON_SED|g" "$HOME/.local/bin/vocotype-fcitx5-backend"
chmod +x "$HOME/.local/bin/vocotype-fcitx5-backend"

# 创建 systemd 用户服务
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/vocotype-fcitx5-backend.service" << EOF
[Unit]
Description=VoCoType Fcitx5 Backend Service
After=graphical-session.target
StartLimitIntervalSec=60
StartLimitBurst=3

[Service]
Type=simple
ExecStart=$HOME/.local/bin/vocotype-fcitx5-backend
# 改进重启策略：任何情况下都重启（包括休眠后被终止）
Restart=on-failure
RestartSec=5s
Environment="PYTHONIOENCODING=UTF-8"

[Install]
WantedBy=default.target
EOF

ensure_desktop_user_environment
if ! command -v systemctl >/dev/null 2>&1; then
    echo "错误: 未检测到 systemctl，无法启动并验收 VoCoType 后台服务。" >&2
    exit 1
fi
if ! systemctl --user daemon-reload; then
    echo "错误: systemd 用户服务定义重载失败。" >&2
    exit 1
fi
systemctl --user reset-failed vocotype-fcitx5-backend.service >/dev/null 2>&1 || true
if ! systemctl --user enable --now vocotype-fcitx5-backend.service; then
    echo "错误: VoCoType 后台服务启动失败。" >&2
    exit 1
fi
echo "✓ 后台服务已启用并启动"

if [ "$PYTHON" = "$PROJECT_DIR/.venv/bin/python" ]; then
    echo "⚠️  当前选择的是项目虚拟环境。若重命名或删除仓库目录，需要重新安装或改用用户级环境。"
fi

echo ""
echo "严格验收将重载 Fcitx 5，并确认 VoCoType addon 实际创建成功。"
echo ""
emit_install_progress 94 "严格验收 Fcitx addon、后台服务与 IPC"
echo "执行安装后严格验收..."
if ! PYTHONPATH="$INSTALL_DIR${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON" "$PROJECT_DIR/installers/validate-installed-integration.py" \
    --framework fcitx5 --runtime-root "$INSTALL_DIR" --timeout 120; then
    echo "错误: VoCoType（Fcitx 5）未达到可运行状态，安装不能标记为成功。" >&2
    exit 1
fi

emit_install_progress 100 "VoCoType（Fcitx 5）程序安装与运行验收完成"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 完成
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ VoCoType（Fcitx 5）安装与运行验收完成"
echo "麦克风回放、真实 ASR 和 AI 试用可在设置中心 Playground 独立完成。"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "无需添加独立输入法条目；继续使用现有输入法，按住 F9 说话。"
echo ""

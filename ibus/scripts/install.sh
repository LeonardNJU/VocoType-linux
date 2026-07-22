#!/bin/bash
# VoCoType Linux IBus 语音输入法安装脚本（用户级安装）
# 基于 VoCoType 核心引擎: https://github.com/233stone/vocotype-cli
#
# 用法: install.sh [--device <id>] [--sample-rate <rate>]
#   --device <id>      指定音频设备ID，跳过交互式配置
#   --sample-rate <rate>  指定采样率（默认44100）

set -e

# 解析命令行参数
AUDIO_DEVICE=""
SAMPLE_RATE="44100"

while [[ $# -gt 0 ]]; do
    case $1 in
        --device)
            AUDIO_DEVICE="$2"
            shift 2
            ;;
        --sample-rate)
            SAMPLE_RATE="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$PROJECT_DIR/installers/runtime-common.sh"

# Python 版本范围（onnxruntime 暂不支持 3.13+）
PYTHON_MIN_MINOR=11
PYTHON_MAX_MINOR=12
DEFAULT_UV_PYTHON="3.12"


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
    echo "    Fedora:       sudo dnf install python3.12"
    echo "    Ubuntu 22.04: sudo apt install python3.12 python3.12-venv"
    echo "    Debian 13:    官方源无 3.12，建议使用 uv"
    echo "    Arch:         sudo pacman -S python312"
    echo ""
    echo "  或使用 conda 创建兼容环境（安装脚本可手动指定解释器）："
    echo "    conda create -n vocotype python=3.12"
    echo "    conda activate vocotype"
}

# IBus is a Python engine. Binary-only wheels remove any need for local
# PyGObject/pycairo compilation; only runtime components are checked here.
check_runtime_deps() {
    local missing=""
    command -v ibus >/dev/null 2>&1 || missing="$missing ibus"
    ldconfig -p 2>/dev/null | grep -q libportaudio || missing="$missing libportaudio2"
    echo "$missing"
}

check_ibus_import() {
    "$1" - << 'PY' >/dev/null 2>&1
import gi
gi.require_version("IBus", "1.0")
from gi.repository import IBus  # noqa: F401
PY
}

# 用户级安装路径
INSTALL_DIR="$HOME/.local/share/vocotype"
COMPONENT_DIR="$HOME/.local/share/ibus/component"
LIBEXEC_DIR="$HOME/.local/libexec"

# OpenAI-compatible API 配置（默认关闭）
ENABLE_SLM=0
SLM_ENDPOINT="http://127.0.0.1:18080/v1/chat/completions"
SLM_MODEL="Qwen/Qwen3.5-0.8B"
SLM_TIMEOUT_MS=20000
SLM_MIN_CHARS=8
SLM_MAX_TOKENS=128
SLM_ENABLE_THINKING=0
SLM_API_KEY=""

echo "=== VoCoType IBus 语音输入法安装 ==="
echo "项目目录: $PROJECT_DIR"
echo "安装目录: $INSTALL_DIR"
echo ""

# 询问是否集成 Rime
echo "请选择安装版本："
echo "  [1] 纯语音版（推荐新手）- 仅语音输入，依赖少"
echo "  [2] 完整版 - 语音 + Rime 拼音输入，一个输入法全搞定"
echo ""
read -r -p "请输入选项 (默认 1): " INSTALL_TYPE

ENABLE_RIME=0
SELECTED_SCHEMA="luna_pinyin"
case "$INSTALL_TYPE" in
    2)
        ENABLE_RIME=1
        echo ""
        echo "您选择了完整版（语音 + Rime 拼音）"
        echo "VoCoType 通过项目内 ctypes 适配层直接调用系统 librime，"
        echo "不编译或安装任何 Python Rime 绑定。"
        echo ""

        if [ -f /etc/fedora-release ] || [ -f /etc/redhat-release ]; then
            DISTRO="Fedora/RHEL"
            INSTALL_CMD="sudo dnf install -y librime librime-tools brise"
        elif [ -f /etc/debian_version ]; then
            DISTRO="Debian/Ubuntu"
            INSTALL_CMD="sudo apt install -y librime1 librime-bin librime-data rime-data-luna-pinyin"
        elif [ -f /etc/arch-release ]; then
            DISTRO="Arch Linux"
            INSTALL_CMD="sudo pacman -S --needed librime librime-data"
        else
            DISTRO="未知"
            INSTALL_CMD=""
        fi

        echo "检测到系统: $DISTRO"
        if command -v rime_deployer >/dev/null 2>&1 &&            ldconfig -p 2>/dev/null | grep -q 'librime\.so'; then
            echo "✓ librime 运行库和部署工具已安装"
        elif [ -n "$INSTALL_CMD" ]; then
            echo "需要安装 Rime 运行依赖："
            echo "  $INSTALL_CMD"
            read -r -p "是否现在自动安装？(y/N): " AUTO_INSTALL
            if [[ "$AUTO_INSTALL" =~ ^[Yy]$ ]]; then
                eval "$INSTALL_CMD" || {
                    echo "❌ 系统依赖安装失败，请手动执行上面的命令。"
                    exit 1
                }
            else
                read -r -p "完成依赖安装后按回车继续，或 Ctrl+C 取消..."
            fi
        else
            echo "未知发行版，请安装 librime、rime_deployer 与 Rime schema 数据。"
            read -r -p "完成依赖安装后按回车继续，或 Ctrl+C 取消..."
        fi

        read -r -p "Rime schema ID (默认 luna_pinyin): " SCHEMA_INPUT
        if [ -n "$SCHEMA_INPUT" ]; then
            SELECTED_SCHEMA="$SCHEMA_INPUT"
        fi
        echo "✓ 将部署输入方案: $SELECTED_SCHEMA"
        ;;
    ""|1|*)
        ENABLE_RIME=0
        echo ""
        echo "您选择了纯语音版"
        ;;
esac

echo ""

echo "是否启用 AI 润色与语音编辑？"
echo "  [1] 不启用（默认）"
echo "  [2] 启用 - 连接 OpenAI-compatible API"
echo ""
read -r -p "请输入选项 (默认 1): " SLM_CHOICE
case "$SLM_CHOICE" in
    2)
        ENABLE_SLM=1
        echo ""
        echo "VoCoType 只调用 OpenAI-compatible API，不启动或管理模型进程。"
        echo "本机 Ollama、llama.cpp、vLLM 与云端服务配置方式完全相同。"
        read -r -p "模型名 (默认 $SLM_MODEL): " SLM_MODEL_INPUT
        if [ -n "$SLM_MODEL_INPUT" ]; then
            SLM_MODEL="$SLM_MODEL_INPUT"
        fi
        read -r -p "API Endpoint (默认 $SLM_ENDPOINT): " SLM_ENDPOINT_INPUT
        if [ -n "$SLM_ENDPOINT_INPUT" ]; then
            SLM_ENDPOINT="$SLM_ENDPOINT_INPUT"
        fi
        read -r -s -p "API Key（无鉴权服务可留空，输入时不回显）: " SLM_API_KEY_INPUT
        echo ""
        if [ -n "$SLM_API_KEY_INPUT" ]; then
            SLM_API_KEY="$SLM_API_KEY_INPUT"
        fi
        ;;
    ""|1|*)
        ENABLE_SLM=0
        echo ""
        echo "已禁用 AI 润色与语音编辑。"
        ;;
esac

echo ""

echo "请选择 Python 环境："
echo "  [1] 使用项目虚拟环境（推荐）: $PROJECT_DIR/.venv"
echo "  [2] 使用用户级虚拟环境: $INSTALL_DIR/.venv"
echo "  [3] 使用系统 Python（省空间，需自行安装依赖）"
echo "  [4] 手动指定 Python 解释器（如 conda 环境）"
read -r -p "请输入选项 (默认 1): " PY_CHOICE

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

        PYTHON="$PROJECT_DIR/.venv/bin/python"
        ;;
    ""|1|*)
        PYTHON="$PROJECT_DIR/.venv/bin/python"
        ;;
esac

if [ "$USE_SYSTEM_PYTHON" = "1" ]; then
    PYTHON_CMD=$(detect_system_python) || {
        echo "错误: 需要 Python 3.11-3.12"
        print_python_help
        exit 1
    }
    PYTHON="$PYTHON_CMD"
    echo "使用系统 Python: $PYTHON_CMD"
else
    if [ -n "$CUSTOM_PYTHON_CMD" ]; then
        PYTHON_CMD="$CUSTOM_PYTHON_CMD"
        echo "使用手动指定的 Python: $PYTHON_CMD ($(get_python_version "$PYTHON_CMD"))"
    elif command -v uv >/dev/null 2>&1; then
        PYTHON_CMD="$DEFAULT_UV_PYTHON"
        echo "检测到 uv，使用 uv 管理 Python: $PYTHON_CMD"
    else
        PYTHON_CMD=$(detect_system_python) || {
            echo "错误: 需要 Python 3.11-3.12"
            print_python_help
            exit 1
        }
        echo "检测到兼容的 Python: $PYTHON_CMD"
    fi
fi

if [ -f /etc/debian_version ]; then
    MISSING_DEPS=$(check_runtime_deps)
    if [ -n "$MISSING_DEPS" ]; then
        echo ""
        echo "⚠️  缺少 IBus 运行时组件：$MISSING_DEPS"
        INSTALL_CMD="sudo apt install -y$MISSING_DEPS"
        read -r -p "是否现在自动安装？(Y/n): " AUTO_INSTALL_DEPS
        if [[ ! "$AUTO_INSTALL_DEPS" =~ ^[Nn]$ ]]; then
            eval "$INSTALL_CMD" || {
                echo "运行时依赖安装失败，请手动执行: $INSTALL_CMD" >&2
                exit 1
            }
        else
            echo "请先执行: $INSTALL_CMD" >&2
            exit 1
        fi
    fi
fi

# 1. 创建目录
echo "[1/6] 创建安装目录与 Python 环境..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$COMPONENT_DIR"
mkdir -p "$LIBEXEC_DIR"

if [ "$USE_SYSTEM_PYTHON" != "1" ] && [ ! -x "$PYTHON" ]; then
    VENV_DIR="$(dirname "$PYTHON")/.."
    echo "创建虚拟环境: $VENV_DIR (使用 $PYTHON_CMD)"
    if command -v uv >/dev/null 2>&1; then
        uv venv --python "$PYTHON_CMD" "$VENV_DIR"
    else
        # Debian/Ubuntu 需要单独安装 python3.x-venv 包
        if [ -f /etc/debian_version ]; then
            py_minor=$("$PYTHON_CMD" -c "import sys; print(sys.version_info.minor)")
            VENV_PKG="python3.${py_minor}-venv"
            if ! "$PYTHON_CMD" -c "import ensurepip" 2>/dev/null; then
                echo ""
                echo "⚠️  缺少 ensurepip 模块，无法创建完整的虚拟环境"
                echo ""
                echo "解决方案："
                echo ""
                echo "  【推荐】安装 uv（自动管理虚拟环境，无需系统 venv 包）："
                echo "    curl -LsSf https://astral.sh/uv/install.sh | sh"
                echo "    然后重新打开终端，再运行本脚本"
                echo ""
                echo "  或尝试安装 $VENV_PKG（Debian 13 官方源可能没有）："
                echo "    sudo apt install $VENV_PKG"
                echo ""
                exit 1
            fi
        fi
        "$PYTHON_CMD" -m venv "$VENV_DIR"
    fi
fi

if [ ! -x "$PYTHON" ]; then
    echo "未找到 Python 可执行文件: $PYTHON"
    echo "请确认已创建虚拟环境或系统已安装 python3。"
    exit 1
fi

if [ "$USE_SYSTEM_PYTHON" = "1" ]; then
    if ! "$PYTHON" "$PROJECT_DIR/installers/check-python-runtime.py"
    then
        echo ""
        echo "系统 Python 无法加载完整的 VoCoType ASR 运行时。"
        echo "请把依赖安装到上面显示的同一个解释器："
        echo "  请重新运行设置中心，让安装器从预构建 wheels 修复运行环境。"
        echo ""
        echo "也可以重新运行安装脚本并选择项目或用户级虚拟环境。"
        exit 1
    fi
else
    echo "安装依赖到虚拟环境..."
    install_runtime_requirements "$PYTHON" "$PROJECT_DIR"
fi

# 验证 IBus GI 绑定可用（避免 Python 3.12 + PyGObject 3.42 导致引擎无法启动）
if ! check_ibus_import "$PYTHON"; then
    echo ""
    echo "⚠️  当前 Python 环境无法导入 gi.repository.IBus，尝试自动修复..."

    if [ "$USE_SYSTEM_PYTHON" = "1" ]; then
        echo "系统 Python 缺少可用 IBus 绑定。请先安装系统包后重试："
        echo "  Fedora: sudo dnf install python3-gobject ibus"
        echo "  Debian/Ubuntu: sudo apt install python3-gi gir1.2-ibus-1.0"
        exit 1
    fi

    if command -v uv >/dev/null 2>&1; then
        install_binary_packages "$PYTHON" "$PROJECT_DIR" "PyGObject>=3.46"
    else
        install_binary_packages "$PYTHON" "$PROJECT_DIR" "PyGObject>=3.46"
    fi

    if ! check_ibus_import "$PYTHON"; then
        echo "❌ 自动修复失败：仍无法导入 gi.repository.IBus"
        echo "   请检查是否安装系统依赖：python3-gi 与 IBus gobject-introspection 包"
        exit 1
    fi

    echo "✓ IBus 绑定修复成功"
fi

download_and_verify_asr_models "$PYTHON" "$PROJECT_DIR" || exit 1

# 2. 音频设备配置
echo "[2/6] 音频设备配置..."

if [ -n "$AUDIO_DEVICE" ]; then
    # 快速安装模式：直接创建配置文件
    echo "使用指定设备 ID: $AUDIO_DEVICE (采样率: $SAMPLE_RATE)"
    CONFIG_DIR="$HOME/.config/vocotype"
    CONFIG_FILE="$CONFIG_DIR/audio.conf"
    mkdir -p "$CONFIG_DIR"
    cat > "$CONFIG_FILE" << EOF
[audio]
device_id = $AUDIO_DEVICE
sample_rate = $SAMPLE_RATE
EOF
    echo "✓ 音频配置已保存到: $CONFIG_FILE"
else
    # 交互式配置
    echo ""
    echo "首先需要配置您的麦克风设备。"
    echo "这个过程会："
    echo "  - 列出可用的音频输入设备"
    echo "  - 测试录音和播放"
    echo "  - 验证语音识别效果"
    echo ""

    if ! "$PYTHON" "$PROJECT_DIR/installers/setup-audio.py"; then
        echo ""
        echo "音频配置失败或被取消。"
        echo "请稍后运行以下命令重新配置："
        echo "  $PYTHON $PROJECT_DIR/installers/setup-audio.py"
        exit 1
    fi
fi

echo ""

# 写入 IBus 运行时 SLM 配置
echo "[可选] 写入 SLM 配置..."
IBUS_RUNTIME_CONFIG="$HOME/.config/vocotype/ibus.json"
write_slm_config_json \
    "$IBUS_RUNTIME_CONFIG" \
    "$PYTHON" \
    "$ENABLE_SLM" \
    "$SLM_ENDPOINT" \
    "$SLM_MODEL" \
    "$SLM_TIMEOUT_MS" \
    "$SLM_MIN_CHARS" \
    "$SLM_MAX_TOKENS" \
    "$SLM_ENABLE_THINKING" \
    "$SLM_API_KEY"
echo "✓ 已写入配置: $IBUS_RUNTIME_CONFIG"

# 3. 复制项目文件
echo "[3/6] 复制项目文件..."
cp -r "$PROJECT_DIR/app" "$INSTALL_DIR/"
cp -r "$PROJECT_DIR/settings_center" "$INSTALL_DIR/"
cp -r "$PROJECT_DIR/ibus" "$INSTALL_DIR/"
cp "$PROJECT_DIR/vocotype_version.py" "$INSTALL_DIR/"
if [ -f "$PROJECT_DIR/data/install-integrity.json" ]; then
    cp "$PROJECT_DIR/data/install-integrity.json" "$INSTALL_DIR/install-integrity.json"
fi
install_native_streaming_bundle "$PROJECT_DIR"

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

# 4. 创建启动脚本
echo "[4/6] 创建启动脚本..."
cat > "$LIBEXEC_DIR/ibus-engine-vocotype" << 'LAUNCHER'
#!/bin/bash
# VoCoType IBus Engine Launcher

VOCOTYPE_HOME="$HOME/.local/share/vocotype"
PROJECT_DIR="VOCOTYPE_PROJECT_DIR"

# 使用项目虚拟环境Python
PYTHON="VOCOTYPE_PYTHON"

export PYTHONPATH="$VOCOTYPE_HOME:$PYTHONPATH"
export PYTHONIOENCODING=UTF-8
export VOCOTYPE_LOG_FILE="$HOME/.local/share/vocotype/ibus.log"

exec $PYTHON "$VOCOTYPE_HOME/ibus/main.py" "$@"
LAUNCHER

# 替换项目目录路径
PROJECT_DIR_SED=$(escape_sed_replacement "$PROJECT_DIR")
PYTHON_SED=$(escape_sed_replacement "$PYTHON")
sed -i "s|VOCOTYPE_PROJECT_DIR|$PROJECT_DIR_SED|g" "$LIBEXEC_DIR/ibus-engine-vocotype"
sed -i "s|VOCOTYPE_PYTHON|$PYTHON_SED|g" "$LIBEXEC_DIR/ibus-engine-vocotype"
chmod +x "$LIBEXEC_DIR/ibus-engine-vocotype"

# 安装统一图形设置中心入口
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/192x192/apps"
cat > "$HOME/.local/bin/vocotype-settings" << 'SETTINGS_LAUNCHER'
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
SETTINGS_LAUNCHER
sed -i "s|VOCOTYPE_PYTHON|$PYTHON_SED|g" "$HOME/.local/bin/vocotype-settings"
INSTALL_DIR_SED=$(escape_sed_replacement "$INSTALL_DIR")
sed -i "s|VOCOTYPE_INSTALL_DIR|$INSTALL_DIR_SED|g" "$HOME/.local/bin/vocotype-settings"
sed -i "s|VOCOTYPE_PROJECT_DIR_VALUE|$PROJECT_DIR_SED|g" "$HOME/.local/bin/vocotype-settings"
chmod +x "$HOME/.local/bin/vocotype-settings"
sed "s|Exec=vocotype-settings|Exec=$HOME/.local/bin/vocotype-settings|" \
    "$PROJECT_DIR/data/applications/io.github.LeonardNJU.VoCoType.Settings.desktop" > \
    "$HOME/.local/share/applications/io.github.LeonardNJU.VoCoType.Settings.desktop"
cp "$PROJECT_DIR/site/icon-192.png" "$HOME/.local/share/icons/hicolor/192x192/apps/vocotype.png"
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
fi
echo "✓ 图形设置中心已安装，可运行: vocotype-settings"

# 5. 配置 Rime 集成（如果启用）
if [ "$ENABLE_RIME" = "1" ]; then
    echo "[5/6] 配置 Rime 集成..."
    VOCOTYPE_RIME_CONFIG="$HOME/.config/vocotype/rime"
    VOCOTYPE_RIME_LOG="$HOME/.local/share/vocotype/rime"
    mkdir -p "$VOCOTYPE_RIME_CONFIG" "$VOCOTYPE_RIME_LOG"

    RIME_SHARED_DIR=""
    for candidate in /usr/share/rime-data /usr/local/share/rime-data; do
        if [ -f "$candidate/default.yaml" ]; then
            RIME_SHARED_DIR="$candidate"
            break
        fi
    done
    [ -n "$RIME_SHARED_DIR" ] || {
        echo "❌ 未找到 Rime 共享数据目录" >&2
        exit 1
    }
    command -v rime_deployer >/dev/null 2>&1 || {
        echo "❌ 未找到 rime_deployer" >&2
        exit 1
    }

    cat > "$VOCOTYPE_RIME_CONFIG/default.custom.yaml" <<EOF
patch:
  schema_list:
    - schema: "$SELECTED_SCHEMA"
EOF
    cat > "$VOCOTYPE_RIME_CONFIG/user.yaml" <<EOF
var:
  previously_selected_schema: "$SELECTED_SCHEMA"
EOF
    rime_deployer --build \
        "$VOCOTYPE_RIME_CONFIG" "$RIME_SHARED_DIR" \
        "$VOCOTYPE_RIME_CONFIG/build" >/dev/null
    [ -f "$VOCOTYPE_RIME_CONFIG/build/default.yaml" ] || {
        echo "❌ Rime 默认配置部署失败" >&2
        exit 1
    }
    [ -f "$VOCOTYPE_RIME_CONFIG/build/$SELECTED_SCHEMA.schema.yaml" ] || {
        echo "❌ Rime schema 部署失败: $SELECTED_SCHEMA" >&2
        exit 1
    }
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$INSTALL_DIR" \
        "$PYTHON" "$INSTALL_DIR/ibus/rime_runtime.py" \
        --shared-data-dir "$RIME_SHARED_DIR" \
        --user-data-dir "$VOCOTYPE_RIME_CONFIG" \
        --log-dir "$VOCOTYPE_RIME_LOG" \
        --schema "$SELECTED_SCHEMA" --key n >/dev/null || {
            echo "❌ Rime 普通键盘输入验收失败" >&2
            exit 1
        }
    echo "✓ Rime 集成可用：$SELECTED_SCHEMA"
else
    echo "[5/6] 跳过 Rime 配置（纯语音版）..."
fi

# 6. 安装IBus组件文件
echo "[6/6] 安装IBus组件配置..."
EXEC_PATH="$LIBEXEC_DIR/ibus-engine-vocotype"
VOCOTYPE_VERSION="2.2.0"
if VOCOTYPE_VERSION=$(PYTHONPATH="$PROJECT_DIR" "$PYTHON" - << 'PY'
from vocotype_version import __version__
print(__version__)
PY
); then
    :
else
    VOCOTYPE_VERSION="2.2.0"
fi

# GNOME 环境下 XDG_DATA_DIRS 不包含用户目录，需要安装到系统目录
SYSTEM_COMPONENT_DIR="/usr/share/ibus/component"
USE_SYSTEM_COMPONENT=0

# 检测是否需要安装到系统目录：
# 1. GNOME 桌面环境
# 2. Debian 系统
# 3. 检测 gnome-shell 进程或包（处理 su/sudo 会话中 XDG_CURRENT_DESKTOP 为空的情况）
if [ "$XDG_CURRENT_DESKTOP" = "GNOME" ] || \
   [ -f /etc/debian_version ] || \
   pgrep -x gnome-shell >/dev/null 2>&1 || \
   command -v gnome-shell >/dev/null 2>&1; then
    echo "检测到 GNOME 环境，IBus 组件需要安装到系统目录"
    USE_SYSTEM_COMPONENT=1
fi

if [ "$USE_SYSTEM_COMPONENT" = "1" ]; then
    sed -e "s|VOCOTYPE_EXEC_PATH|$EXEC_PATH|g" \
        -e "s|VOCOTYPE_VERSION|$VOCOTYPE_VERSION|g" \
        "$PROJECT_DIR/ibus/data/vocotype.xml.in" > "/tmp/vocotype.xml"

    if sudo cp "/tmp/vocotype.xml" "$SYSTEM_COMPONENT_DIR/vocotype.xml"; then
        echo "✓ IBus 组件已安装到 $SYSTEM_COMPONENT_DIR"
        rm -f "/tmp/vocotype.xml"
    else
        echo ""
        echo "❌ 无法安装到系统目录（需要 sudo 权限）"
        echo ""
        echo "组件文件已保存到 /tmp/vocotype.xml"
        echo "请使用有 sudo 权限的用户执行以下命令："
        echo "  sudo cp /tmp/vocotype.xml $SYSTEM_COMPONENT_DIR/"
        echo ""
        echo "然后重新运行安装脚本完成剩余配置。"
        exit 1
    fi
else
    mkdir -p "$COMPONENT_DIR"
    sed -e "s|VOCOTYPE_EXEC_PATH|$EXEC_PATH|g" \
        -e "s|VOCOTYPE_VERSION|$VOCOTYPE_VERSION|g" \
        "$PROJECT_DIR/ibus/data/vocotype.xml.in" > "$COMPONENT_DIR/vocotype.xml"
fi

if command -v ibus >/dev/null 2>&1 && [[ -n "${DBUS_SESSION_BUS_ADDRESS:-}${XDG_RUNTIME_DIR:-}" ]]; then
    echo "刷新 IBus 注册信息..."
    if command -v timeout >/dev/null 2>&1; then
        timeout 12s ibus restart >/dev/null 2>&1 || \
            echo "⚠️ IBus 当前会话未能自动重启；继续验证安装结构。"
    else
        ibus restart >/dev/null 2>&1 || \
            echo "⚠️ IBus 当前会话未能自动重启；继续验证安装结构。"
    fi
fi

echo "执行安装后严格验收..."
if ! PYTHONPATH="$INSTALL_DIR${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON" "$PROJECT_DIR/installers/validate-installed-integration.py" \
    --framework ibus --runtime-root "$INSTALL_DIR"; then
    echo "错误: VoCoType（IBus）未达到完整安装状态。" >&2
    exit 1
fi

echo ""
echo "=== ✅ VoCoType（IBus）安装与运行验收完成 ==="
echo "麦克风回放、真实 ASR 和 AI 试用可在设置中心 Playground 独立完成。"
echo ""

if [ "$ENABLE_RIME" = "1" ]; then
    echo "✨ 已安装语音 + Rime 集成"
else
    echo "🎤 已安装纯语音集成"
fi

echo ""
echo "请执行以下步骤完成配置："
echo ""
echo "1. 重启IBus:"
echo "   ibus restart"
echo ""
echo "2. 添加输入法:"
echo "   设置 → 键盘 → 输入源 → +"
echo "   → 滑到最底下点三个点(⋮)"
echo "   → 搜索 'voco' → 中文 → VoCoType Voice Input"
echo ""

if [ "$ENABLE_RIME" = "1" ]; then
    echo "3. 使用方法（完整版）:"
    echo "   - 切换到VoCoType输入法"
    echo "   - 语音输入：按住F9说话，松开后自动识别并输入"
    echo "   - 拼音输入：直接打字，Rime会显示候选词"
    echo ""
    echo "配置说明："
    echo "   - Rime 配置目录: ~/.config/ibus/rime/"
    echo "   - 当前输入方案: $SELECTED_SCHEMA"
    echo "   - 如需更换方案，请编辑 ~/.config/vocotype/rime/user.yaml"
else
    echo "3. 使用方法（纯语音版）:"
    echo "   - 切换到VoCoType输入法"
    echo "   - 按住F9说话，松开后自动识别并输入"
    echo ""
    echo "提示："
    echo "   - 如需拼音输入，请重新运行安装器并选择完整版"
    echo "   - 如果以后想升级到完整版，请重新运行安装脚本并选择选项 2"
fi

echo ""

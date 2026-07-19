#!/bin/bash
# VoCoType Linux IBus 语音输入法安装脚本（用户级安装）
# 基于 VoCoType 核心引擎: https://github.com/233stone/vocotype-cli
#
# 用法: install-ibus.sh [--device <id>] [--sample-rate <rate>]
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
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Python 版本范围（onnxruntime 暂不支持 3.13+）
PYTHON_MIN_MINOR=11
PYTHON_MAX_MINOR=12
DEFAULT_UV_PYTHON="3.12"

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

get_python_version() {
    "$1" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null
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

# 检测系统可用的 Python 版本（需要 3.11-3.12）
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

write_slm_config_json() {
    local config_file="$1"
    local python_bin="$2"
    local enabled="$3"
    local provider="$4"
    local endpoint="$5"
    local model="$6"
    local local_model="$7"
    local local_python="$8"
    local timeout_ms="$9"
    local min_chars="${10}"
    local max_tokens="${11}"
    local warmup_timeout_ms="${12}"
    local enable_thinking="${13}"
    local api_key="${14}"

    "$python_bin" - "$config_file" "$enabled" "$provider" "$endpoint" "$model" "$local_model" "$local_python" "$timeout_ms" "$min_chars" "$max_tokens" "$warmup_timeout_ms" "$enable_thinking" "$api_key" << 'PY'
import json
import os
import sys
from typing import Any


def load_json(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


target = os.path.expanduser(sys.argv[1])
enabled = bool(int(sys.argv[2]))
provider = sys.argv[3]
endpoint = sys.argv[4]
model = sys.argv[5]
local_model = sys.argv[6]
local_python = sys.argv[7]
timeout_ms = int(sys.argv[8])
min_chars = int(sys.argv[9])
max_tokens = int(sys.argv[10])
warmup_timeout_ms = int(sys.argv[11])
enable_thinking = bool(int(sys.argv[12]))
api_key = sys.argv[13]

cfg = load_json(target)
slm = cfg.get("slm", {})
if not isinstance(slm, dict):
    slm = {}

slm.update(
    {
        "enabled": enabled,
        "provider": provider,
        "model": model,
        "local_model": local_model,
        "local_python": local_python,
        "timeout_ms": timeout_ms,
        "warmup_timeout_ms": warmup_timeout_ms,
        "min_chars": min_chars,
        "max_tokens": max_tokens,
        "enable_thinking": enable_thinking,
        "api_key": api_key,
    }
)
if provider == "remote":
    slm["endpoint"] = endpoint
else:
    slm.pop("endpoint", None)
cfg["slm"] = slm

os.makedirs(os.path.dirname(target), exist_ok=True)
with open(target, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY
}

# 检测 IBus 引擎必需的系统构建依赖（用于编译 pycairo/pygobject）
check_build_deps() {
    local missing=""

    if ! command -v pkg-config >/dev/null 2>&1; then
        missing="$missing pkg-config"
    fi

    # 检测 cairo 开发库
    if ! pkg-config --exists cairo 2>/dev/null; then
        missing="$missing libcairo2-dev"
    fi

    # PyGObject < 3.52 使用 gobject-introspection-1.0；该名称同时适用于
    # Ubuntu 22.04 与 24.04 的 libgirepository1.0-dev。
    if ! pkg-config --exists gobject-introspection-1.0 2>/dev/null; then
        missing="$missing libgirepository1.0-dev"
    fi

    # 检测 PortAudio 运行时库（sounddevice 需要）
    if ! ldconfig -p 2>/dev/null | grep -q libportaudio; then
        missing="$missing libportaudio2"
    fi

    # 检测 Python 开发头文件（仅当使用系统 Python 或没有 uv 时需要）
    if [ "$USE_SYSTEM_PYTHON" = "1" ] || ! command -v uv >/dev/null 2>&1; then
        py_version=$("$PYTHON_CMD" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        if ! pkg-config --exists "python-${py_version}-embed" 2>/dev/null && \
           ! pkg-config --exists "python-${py_version}" 2>/dev/null && \
           ! "$PYTHON_CMD" -c "import sysconfig; exit(0 if sysconfig.get_config_var('INCLUDEPY') and __import__('os').path.exists(sysconfig.get_config_var('INCLUDEPY') + '/Python.h') else 1)" 2>/dev/null; then
            missing="$missing python${py_version}-dev"
        fi
    fi

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
case "$INSTALL_TYPE" in
    2)
        ENABLE_RIME=1
        echo ""
        echo "您选择了完整版（语音 + Rime 拼音）"
        echo ""
        echo "完整版需要额外依赖："
        echo "  - librime-devel (Rime 开发库)"
        echo "  - pyrime (Python 绑定，自动安装)"
        echo ""

        # 检测系统类型并提供安装命令
        if [ -f /etc/fedora-release ] || [ -f /etc/redhat-release ]; then
            DISTRO="Fedora/RHEL"
            INSTALL_CMD="sudo dnf install -y librime-devel ibus-rime"
            CHECK_CMD="rpm -q librime-devel"
        elif [ -f /etc/debian_version ]; then
            DISTRO="Debian/Ubuntu"
            INSTALL_CMD="sudo apt install -y librime-dev ibus-rime"
            CHECK_CMD="dpkg -l librime-dev"
        elif [ -f /etc/arch-release ]; then
            DISTRO="Arch Linux"
            INSTALL_CMD="sudo pacman -S --noconfirm librime ibus-rime"
            CHECK_CMD="pacman -Q librime"
        else
            DISTRO="未知"
            INSTALL_CMD=""
            CHECK_CMD=""
        fi

        echo "检测到系统: $DISTRO"
        echo ""

        # 检查 librime 是否已安装
        if [ -n "$CHECK_CMD" ] && eval "$CHECK_CMD" >/dev/null 2>&1; then
            echo "✓ librime 开发库已安装"
        else
            echo "⚠️  未检测到 librime 开发库"
            echo ""

            if [ -n "$INSTALL_CMD" ]; then
                echo "需要安装系统依赖，建议执行："
                echo "  $INSTALL_CMD"
                echo ""
                read -r -p "是否现在自动安装？(y/N): " AUTO_INSTALL

                if [[ "$AUTO_INSTALL" =~ ^[Yy]$ ]]; then
                    echo "正在安装系统依赖..."
                    if eval "$INSTALL_CMD"; then
                        echo "✓ 系统依赖安装成功"
                    else
                        echo "❌ 系统依赖安装失败"
                        echo "   请手动执行: $INSTALL_CMD"
                        echo "   然后重新运行安装脚本"
                        exit 1
                    fi
                else
                    echo ""
                    echo "请先手动安装系统依赖："
                    echo "  $INSTALL_CMD"
                    echo ""
                    read -r -p "已完成安装？按回车继续，或 Ctrl+C 取消..."
                fi
            else
                echo "未知的发行版，请手动安装 librime 开发库"
                echo "参考: https://github.com/rime/librime"
                echo ""
                read -r -p "已完成安装？按回车继续，或 Ctrl+C 取消..."
            fi
        fi

        echo ""
        ;;
    ""|1|*)
        ENABLE_RIME=0
        echo ""
        echo "您选择了纯语音版"
        ;;
esac

echo ""

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
    MISSING_DEPS=$(check_build_deps)
    if [ -n "$MISSING_DEPS" ]; then
        echo ""
        echo "⚠️  缺少编译 IBus 引擎依赖所需的系统库"
        echo ""
        INSTALL_CMD="sudo apt install -y$MISSING_DEPS"
        echo "需要安装：$MISSING_DEPS"
        echo ""
        read -r -p "是否现在自动安装？(Y/n): " AUTO_INSTALL_DEPS
        if [[ ! "$AUTO_INSTALL_DEPS" =~ ^[Nn]$ ]]; then
            echo "正在安装系统依赖..."
            if eval "$INSTALL_CMD"; then
                echo "✓ 系统依赖安装成功"
            else
                echo "❌ 系统依赖安装失败"
                echo "   请手动执行: $INSTALL_CMD"
                exit 1
            fi
        else
            echo "请先安装系统依赖："
            echo "  $INSTALL_CMD"
            exit 1
        fi
        echo ""
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
    if ! "$PYTHON" "$PROJECT_DIR/scripts/check-python-runtime.py"
    then
        echo ""
        echo "系统 Python 无法加载完整的 VoCoType ASR 运行时。"
        echo "请把依赖安装到上面显示的同一个解释器："
        echo "  $PYTHON -m pip install -r $PROJECT_DIR/requirements.txt"
        echo ""
        echo "也可以重新运行安装脚本并选择项目或用户级虚拟环境。"
        exit 1
    fi
else
    echo "安装依赖到虚拟环境..."
    if command -v uv >/dev/null 2>&1; then
        uv pip install --python "$PYTHON" -r "$PROJECT_DIR/requirements.txt"
    else
        "$PYTHON" -m pip install -r "$PROJECT_DIR/requirements.txt"
    fi

    # 如果启用 Rime，安装 pyrime
    if [ "$ENABLE_RIME" = "1" ]; then
        echo ""
        echo "安装 pyrime（Rime Python 绑定）..."
        if command -v uv >/dev/null 2>&1; then
            if ! uv pip install --python "$PYTHON" pyrime; then
                echo "⚠️  pyrime 安装失败"
                echo "   这可能是因为 librime-devel 未正确安装"
                echo "   VoCoType 将以纯语音模式运行"
                ENABLE_RIME=0
            fi
        else
            if ! "$PYTHON" -m pip install pyrime; then
                echo "⚠️  pyrime 安装失败"
                echo "   这可能是因为 librime-devel 未正确安装"
                echo "   VoCoType 将以纯语音模式运行"
                ENABLE_RIME=0
            fi
        fi

        # pyrime 安装成功后，进行 schema 选择
        if [ "$ENABLE_RIME" = "1" ]; then
            echo ""
            echo "══════════════════════════════════════════"
            echo "   RIME 输入方案配置"
            echo "══════════════════════════════════════════"
            echo ""
            # 检测已部署的 schema（从 build 目录）
            IBUS_RIME_USER="$HOME/.config/ibus/rime"
            IBUS_RIME_BUILD="$IBUS_RIME_USER/build"
            declare -a SCHEMAS=()
            declare -A SCHEMA_NAMES=()

            # 常见 schema 的中文名称
            SCHEMA_NAMES["luna_pinyin"]="朙月拼音"
            SCHEMA_NAMES["luna_pinyin_simp"]="朙月拼音·简化字"
            SCHEMA_NAMES["luna_pinyin_tw"]="朙月拼音·臺灣正體"
            SCHEMA_NAMES["double_pinyin"]="自然码双拼"
            SCHEMA_NAMES["double_pinyin_abc"]="智能ABC双拼"
            SCHEMA_NAMES["double_pinyin_flypy"]="小鹤双拼"
            SCHEMA_NAMES["double_pinyin_mspy"]="微软双拼"
            SCHEMA_NAMES["double_pinyin_pyjj"]="拼音加加双拼"
            SCHEMA_NAMES["rime_ice"]="雾凇拼音"
            SCHEMA_NAMES["wubi86"]="五笔86"
            SCHEMA_NAMES["wubi98"]="五笔98"
            SCHEMA_NAMES["wubi_pinyin"]="五笔拼音混输"
            SCHEMA_NAMES["pinyin_simp"]="袖珍简化字拼音"
            SCHEMA_NAMES["terra_pinyin"]="地球拼音"
            SCHEMA_NAMES["bopomofo"]="注音"
            SCHEMA_NAMES["bopomofo_tw"]="注音·臺灣正體"
            SCHEMA_NAMES["bopomofo_express"]="注音·快打"
            SCHEMA_NAMES["cangjie5"]="仓颉五代"
            SCHEMA_NAMES["cangjie5_express"]="仓颉五代·快打"
            SCHEMA_NAMES["quick5"]="速成"
            SCHEMA_NAMES["stroke"]="五笔画"
            SCHEMA_NAMES["array30"]="行列30"
            SCHEMA_NAMES["combo_pinyin"]="宫保拼音"
            SCHEMA_NAMES["combo_pinyin_kbcon"]="宫保拼音·键盘控"
            SCHEMA_NAMES["combo_pinyin_left"]="宫保拼音·左手"
            SCHEMA_NAMES["stenotype"]="打字速记"
            SCHEMA_NAMES["jyutping"]="粤拼"
            SCHEMA_NAMES["ipa_xsampa"]="国际音标"
            SCHEMA_NAMES["emoji"]="绘文字"
            SCHEMA_NAMES["stroke_simp"]="笔顺·简化字"
            SCHEMA_NAMES["triungkox"]="中古汉语三拼"

            # 扫描已部署的 schema（从 build 目录的 .prism.bin 文件）
            if [ -d "$IBUS_RIME_BUILD" ]; then
                for f in "$IBUS_RIME_BUILD"/*.prism.bin; do
                    if [ -f "$f" ]; then
                        schema_id=$(basename "$f" .prism.bin)
                        SCHEMAS+=("$schema_id")
                    fi
                done
            fi

            # 检查是否有已部署的 schema
            if [ ${#SCHEMAS[@]} -eq 0 ]; then
                echo "⚠️  未检测到已部署的 Rime 输入方案"
                echo ""
                echo "请先运行 ibus-rime 完成 Rime 部署："
                echo "  1. 添加 ibus-rime 输入法"
                echo "  2. 切换到 ibus-rime 并使用一次"
                echo "  3. Rime 会自动部署输入方案"
                echo "  4. 然后重新运行本安装脚本"
                echo ""
                echo "⚠️  Rime 功能将被禁用，VoCoType 将以纯语音模式运行"
                ENABLE_RIME=0
            elif [ ${#SCHEMAS[@]} -gt 0 ]; then
                echo "检测到 ${#SCHEMAS[@]} 个已部署的输入方案"
                echo ""
                # 优先显示 luna_pinyin
                MENU_SCHEMAS=()
                if [[ " ${SCHEMAS[*]} " =~ " luna_pinyin " ]]; then
                    MENU_SCHEMAS+=("luna_pinyin")
                fi
                for s in "${SCHEMAS[@]}"; do
                    if [ "$s" != "luna_pinyin" ]; then
                        MENU_SCHEMAS+=("$s")
                    fi
                done

                # 翻页显示
                PAGE_SIZE=10
                TOTAL=${#MENU_SCHEMAS[@]}
                PAGE=0
                TOTAL_PAGES=$(( (TOTAL + PAGE_SIZE - 1) / PAGE_SIZE ))

                while true; do
                    echo "检测到以下可用输入方案（第 $((PAGE + 1))/$TOTAL_PAGES 页，共 $TOTAL 个）："
                    echo ""

                    START=$((PAGE * PAGE_SIZE))
                    END=$((START + PAGE_SIZE))
                    if [ $END -gt $TOTAL ]; then
                        END=$TOTAL
                    fi

                    count=0
                    for ((i = START; i < END; i++)); do
                        s="${MENU_SCHEMAS[$i]}"
                        count=$((count + 1))
                        display_num=$((i + 1))
                        name="${SCHEMA_NAMES[$s]:-$s}"
                        if [ "$s" = "luna_pinyin" ]; then
                            echo "  [$display_num] $s - $name（推荐，librime 自带）"
                        else
                            echo "  [$display_num] $s - $name"
                        fi
                    done

                    echo ""
                    if [ $TOTAL_PAGES -gt 1 ]; then
                        echo "  [n] 下一页  [p] 上一页"
                    fi
                    echo "  [0] 使用默认 luna_pinyin"
                    echo ""

                    read -r -p "请输入方案编号 (默认 1): " SCHEMA_CHOICE

                    # 翻页处理
                    if [ "$SCHEMA_CHOICE" = "n" ] || [ "$SCHEMA_CHOICE" = "N" ]; then
                        if [ $((PAGE + 1)) -lt $TOTAL_PAGES ]; then
                            PAGE=$((PAGE + 1))
                            echo ""
                            continue
                        else
                            echo "已是最后一页"
                            echo ""
                            continue
                        fi
                    elif [ "$SCHEMA_CHOICE" = "p" ] || [ "$SCHEMA_CHOICE" = "P" ]; then
                        if [ $PAGE -gt 0 ]; then
                            PAGE=$((PAGE - 1))
                            echo ""
                            continue
                        else
                            echo "已是第一页"
                            echo ""
                            continue
                        fi
                    fi

                    # 选择处理
                    if [ -z "$SCHEMA_CHOICE" ] || [ "$SCHEMA_CHOICE" = "1" ]; then
                        SELECTED_SCHEMA="${MENU_SCHEMAS[0]}"
                        break
                    elif [ "$SCHEMA_CHOICE" = "0" ]; then
                        SELECTED_SCHEMA="luna_pinyin"
                        break
                    elif [[ "$SCHEMA_CHOICE" =~ ^[0-9]+$ ]] && [ "$SCHEMA_CHOICE" -ge 1 ] && [ "$SCHEMA_CHOICE" -le $TOTAL ]; then
                        idx=$((SCHEMA_CHOICE - 1))
                        SELECTED_SCHEMA="${MENU_SCHEMAS[$idx]}"
                        break
                    else
                        echo "无效选择，请重新输入"
                        echo ""
                        continue
                    fi
                done
            else
                echo "未检测到可用的输入方案，将使用默认方案 luna_pinyin"
                SELECTED_SCHEMA="luna_pinyin"
            fi

            echo ""
            echo "✓ 已选择输入方案: $SELECTED_SCHEMA"
            echo "══════════════════════════════════════════"
            echo ""
        fi
    fi
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
        uv pip install --python "$PYTHON" -U "PyGObject>=3.56.0"
    else
        "$PYTHON" -m pip install -U "PyGObject>=3.56.0"
    fi

    if ! check_ibus_import "$PYTHON"; then
        echo "❌ 自动修复失败：仍无法导入 gi.repository.IBus"
        echo "   请检查是否安装系统依赖：python3-gi 与 IBus gobject-introspection 包"
        exit 1
    fi

    echo "✓ IBus 绑定修复成功"
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
    else
        if command -v uv >/dev/null 2>&1; then
            uv pip install --python "$PYTHON" torch transformers sentencepiece socksio
        else
            "$PYTHON" -m pip install torch transformers sentencepiece socksio
        fi
    fi
fi

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

    if ! "$PYTHON" "$PROJECT_DIR/scripts/setup-audio.py"; then
        echo ""
        echo "音频配置失败或被取消。"
        echo "请稍后运行以下命令重新配置："
        echo "  $PYTHON $PROJECT_DIR/scripts/setup-audio.py"
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
echo "✓ 已写入配置: $IBUS_RUNTIME_CONFIG"

# 3. 复制项目文件
echo "[3/6] 复制项目文件..."
cp -r "$PROJECT_DIR/app" "$INSTALL_DIR/"
cp -r "$PROJECT_DIR/ibus" "$INSTALL_DIR/"
cp "$PROJECT_DIR/vocotype_version.py" "$INSTALL_DIR/"

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
sed -i "s|VOCOTYPE_PROJECT_DIR|$PROJECT_DIR|g" "$LIBEXEC_DIR/ibus-engine-vocotype"
sed -i "s|VOCOTYPE_PYTHON|$PYTHON|g" "$LIBEXEC_DIR/ibus-engine-vocotype"
chmod +x "$LIBEXEC_DIR/ibus-engine-vocotype"

# 5. 配置 Rime 集成（如果启用）
if [ "$ENABLE_RIME" = "1" ]; then
    echo "[5/6] 配置 Rime 集成..."

    # 使用 ibus-rime 配置目录（保证 Rime 完整可用）
    VOCOTYPE_RIME_CONFIG="$HOME/.config/vocotype/rime"
    VOCOTYPE_RIME_LOG="$HOME/.local/share/vocotype/rime"
    IBUS_RIME_DIR="$HOME/.config/ibus/rime"

    mkdir -p "$VOCOTYPE_RIME_CONFIG"
    mkdir -p "$VOCOTYPE_RIME_LOG"
    mkdir -p "$IBUS_RIME_DIR"

    # 检查系统 Rime 数据是否存在（必需）
    if [ ! -f "/usr/share/rime-data/default.yaml" ] && [ ! -f "/usr/local/share/rime-data/default.yaml" ]; then
        echo ""
        echo "❌ 未找到系统 Rime 配置文件"
        echo "   请确认 rime-data 已安装"
        echo ""
        echo "   Fedora/RHEL: sudo dnf install rime-data"
        echo "   Debian/Ubuntu: sudo apt install librime-data-*"
        echo "   Arch: sudo pacman -S rime-data"
        echo ""
        echo "⚠️  Rime 功能将被禁用，VoCoType 将以纯语音模式运行"
        ENABLE_RIME=0
    else
        # 创建 user.yaml（仅用于记录用户选择的方案）
        cat > "$VOCOTYPE_RIME_CONFIG/user.yaml" << EOF
# VoCoType RIME 用户配置
# 如需更换输入方案，请修改下面的 previously_selected_schema 值
var:
  previously_selected_schema: "$SELECTED_SCHEMA"
EOF
        echo "  创建配置文件: $VOCOTYPE_RIME_CONFIG/user.yaml"

        echo ""
        echo "✓ Rime 集成配置完成"
        if [ -f "$IBUS_RIME_DIR/default.yaml" ]; then
            echo "  用户配置: $IBUS_RIME_DIR/default.yaml"
        else
            echo "  系统配置: /usr/share/rime-data/default.yaml"
        fi
        echo "  用户目录: $IBUS_RIME_DIR"
        echo "  输入方案: $SELECTED_SCHEMA"
    fi
    echo ""
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
        "$PROJECT_DIR/data/ibus/vocotype.xml.in" > "/tmp/vocotype.xml"

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
        "$PROJECT_DIR/data/ibus/vocotype.xml.in" > "$COMPONENT_DIR/vocotype.xml"
fi

echo ""
echo "=== 安装完成 ==="
echo ""

if [ "$ENABLE_RIME" = "1" ]; then
    echo "✨ 已安装完整版（语音 + Rime 拼音）"
else
    echo "🎤 已安装纯语音版"
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
    echo "   - 如需拼音输入，请安装并切换到其他拼音输入法（如 ibus-rime）"
    echo "   - 如果以后想升级到完整版，请重新运行安装脚本并选择选项 2"
fi

echo ""

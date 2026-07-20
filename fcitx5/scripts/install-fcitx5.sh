#!/bin/bash
# VoCoType Fcitx 5 安装脚本
#
# 用法: install-fcitx5.sh [--device <id>] [--sample-rate <rate>] [--skip-audio]
#   --device <id>      指定音频设备ID，跳过交互式配置
#   --sample-rate <rate>  指定采样率（默认44100）
#   --skip-audio       跳过音频配置
#
# 历史问题修复记录：
# 1. FCITX_ADDON_DIRS 环境变量 - Fcitx5 默认不搜索 ~/.local/lib64/fcitx5
# 2. 库文件前缀 - 需要创建 libvocotype.so 符号链接
# 3. inputmethod 配置 - 文件扩展名应为 .conf（不是 .conf.in）
# 4. listInputMethods() - C++ 代码必须实现此方法才能被 Fcitx5 发现
# 5. C++20 标准 - Fcitx5 日志宏需要 source_location

set -e

# 解析命令行参数
SKIP_AUDIO=false
AUDIO_DEVICE=""
SAMPLE_RATE="44100"

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
        *)
            shift
            ;;
    esac
done

PROJECT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../.." && pwd)"
INSTALL_DIR="$HOME/.local/share/vocotype-fcitx5"
SCRIPT_DIR="$PROJECT_DIR/scripts"
INSTALLED_SETUP_AUDIO_SCRIPT="$INSTALL_DIR/scripts/setup-audio.py"
PYTHON_MIN_MINOR=11
PYTHON_MAX_MINOR=12
DEFAULT_UV_PYTHON="3.12"

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
    echo "    Fedora: sudo dnf install python3.12"
    echo "    Ubuntu: sudo apt install python3.12"
    echo "    Arch:   sudo pacman -S python312"
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

echo "=== VoCoType Fcitx 5 语音输入法安装 ==="
echo "项目目录: $PROJECT_DIR"
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

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. 检查 Fcitx 5
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo "[1/8] 检查 Fcitx 5..."
if ! command -v fcitx5 &>/dev/null; then
    echo "错误: 未检测到 Fcitx 5"
    echo "请先安装 Fcitx 5:"
    echo "  Debian/Ubuntu: sudo apt install fcitx5 fcitx5-config-qt"
    echo "  Fedora:        sudo dnf install fcitx5 fcitx5-configtool"
    echo "  Arch:          sudo pacman -S fcitx5 fcitx5-configtool"
    exit 1
fi
echo "✓ Fcitx 5 已安装"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. 检查编译依赖
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo ""
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
    echo "错误: 缺少以下依赖:"
    for dep in "${missing_deps[@]}"; do
        echo "  - $dep"
    done
    echo ""
    echo "安装命令参考:"
    echo "  Debian/Ubuntu: sudo apt install cmake pkg-config libfcitx5-dev nlohmann-json3-dev"
    echo "  Fedora:        sudo dnf install cmake pkgconfig fcitx5-devel json-devel"
    echo "  Arch:          sudo pacman -S cmake pkgconfig fcitx5 nlohmann-json"
    exit 1
fi
echo "✓ 编译依赖已满足"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. 编译 C++ 全局 Module
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo ""
echo "[3/8] 编译 C++ 全局 Module..."
mkdir -p "$PROJECT_DIR/fcitx5/module/build"
cd "$PROJECT_DIR/fcitx5/module/build"

cmake .. -DCMAKE_INSTALL_PREFIX="$HOME/.local"
make -j$(nproc)
echo "✓ 编译成功"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. 安装 C++ 全局 Module（多位置 + 符号链接）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo ""
echo "[4/8] 安装 C++ 全局 Module..."
make install

# 复制到 lib 目录（某些 Fcitx5 配置可能需要）
mkdir -p "$HOME/.local/lib/fcitx5"
cp "$HOME/.local/lib64/fcitx5/vocotype.so" "$HOME/.local/lib/fcitx5/" 2>/dev/null || \
cp "$PROJECT_DIR/fcitx5/module/build/vocotype.so" "$HOME/.local/lib/fcitx5/"

# 创建 lib 前缀的符号链接（兼容性）
if [ -d "$HOME/.local/lib64/fcitx5" ]; then
    ln -sf "$HOME/.local/lib64/fcitx5/vocotype.so" "$HOME/.local/lib64/fcitx5/libvocotype.so"
fi
if [ -d "$HOME/.local/lib/fcitx5" ]; then
    ln -sf "$HOME/.local/lib/fcitx5/vocotype.so" "$HOME/.local/lib/fcitx5/libvocotype.so"
fi

# 安装 Addon 配置文件
mkdir -p "$HOME/.local/share/fcitx5/addon"
cp "$PROJECT_DIR/fcitx5/data/vocotype.conf" "$HOME/.local/share/fcitx5/addon/"

# 清理旧版独立输入法条目；新版作为全局 Module 在所有输入法下生效。
rm -f "$HOME/.local/share/fcitx5/inputmethod/vocotype.conf"

echo "✓ C++ 全局 Module 已安装"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. 设置环境变量（关键：让 Fcitx5 找到用户插件）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo ""
echo "[5/8] 配置环境变量..."
mkdir -p "$HOME/.config/environment.d"
cat > "$HOME/.config/environment.d/fcitx5-vocotype.conf" << 'EOF'
FCITX_ADDON_DIRS=$HOME/.local/lib64/fcitx5:$HOME/.local/lib/fcitx5:/usr/lib64/fcitx5:/usr/lib/x86_64-linux-gnu/fcitx5:/usr/lib/fcitx5
EOF
echo "✓ 环境变量已配置"
echo "  注意: 需要重新登录或设置环境变量才能生效"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. 安装 Python 后端
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo ""
echo "[6/8] 安装 Python 后端..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR/scripts"

# 复制文件
cp -r "$PROJECT_DIR/app" "$INSTALL_DIR/"
cp -r "$PROJECT_DIR/fcitx5/backend" "$INSTALL_DIR/"
cp "$PROJECT_DIR/vocotype_version.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/setup-audio.py" "$INSTALLED_SETUP_AUDIO_SCRIPT"

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
echo "[7/8] 配置 Python 环境..."

echo "请选择 Python 环境："
echo "  [1] 使用项目虚拟环境（开发用，依赖当前仓库）: $PROJECT_DIR/.venv"
echo "  [2] 使用用户级虚拟环境（默认，删除工作区后仍可用）: $INSTALL_DIR/.venv"
echo "  [3] 使用系统 Python（省空间，需自行安装依赖）"
echo "  [4] 手动指定 Python 解释器（如 conda 环境）"
read -r -p "请输入选项 (默认 2): " PY_CHOICE

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
    elif command -v uv &>/dev/null; then
        PYTHON_CMD="$DEFAULT_UV_PYTHON"
        echo "检测到 uv，使用 uv 管理 Python: $PYTHON_CMD"
    else
        PYTHON_CMD=$(detect_system_python) || {
            echo "错误: 需要 Python 3.11-3.12"
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
import librosa  # noqa: F401
import modelscope  # noqa: F401
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
FCITX5_BACKEND_CONFIG="$HOME/.config/vocotype/fcitx5-backend.json"
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

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 8. 音频设备配置和 ASR 验收
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo ""
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
    # 跳过音频配置
    echo "跳过音频配置（使用 --skip-audio）"
    echo "请稍后运行以下命令配置音频："
    echo "  $PYTHON $INSTALLED_SETUP_AUDIO_SCRIPT"
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
        echo "⚠️  音频配置未完成。"
        echo "请稍后运行以下命令重新配置："
        echo "  $PYTHON $INSTALLED_SETUP_AUDIO_SCRIPT"
        echo ""
        read -p "是否继续安装？ [y/N] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            echo "安装已取消"
            exit 1
        fi
    fi
fi

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 创建后台服务启动器
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo ""
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

# 检查是否已在运行
if pgrep -f "fcitx5_server.py" > /dev/null; then
    echo "VoCoType Fcitx5 Backend 已在运行"
    exit 0
fi

# 启动服务
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

[Service]
Type=simple
ExecStart=$HOME/.local/bin/vocotype-fcitx5-backend
# 改进重启策略：任何情况下都重启（包括休眠后被终止）
Restart=always
RestartSec=5s
Environment="PYTHONIOENCODING=UTF-8"

[Install]
WantedBy=default.target
EOF

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload >/dev/null 2>&1 || \
        echo "⚠️  systemctl --user daemon-reload 失败，请手动执行"
fi

echo "✓ 后台服务启动器已创建"
if [ "$PYTHON" = "$PROJECT_DIR/.venv/bin/python" ]; then
    echo "⚠️  当前选择的是项目虚拟环境。若重命名或删除仓库目录，需要重新安装或改用选项 2/3/4。"
fi

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 完成
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ VoCoType Fcitx 5 安装完成！"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "📝 接下来的步骤："
echo ""
echo "1. 【重要】设置环境变量（选择一种方式）："
echo ""
echo "   方式 A - 重新登录（推荐）"
echo "     注销并重新登录桌面会话"
echo ""
echo "   方式 B - 当前终端临时设置"
echo "     export FCITX_ADDON_DIRS=~/.local/lib64/fcitx5:~/.local/lib/fcitx5:/usr/lib64/fcitx5"
echo ""
echo "2. 启动后台服务："
echo ""
echo "   systemctl --user daemon-reload"
echo "   systemctl --user enable --now vocotype-fcitx5-backend.service"
echo ""
echo "3. 重启 Fcitx 5："
echo "     fcitx5 -r"
echo ""
echo "4. 确认 VoCoType 全局 Module 已启用："
echo "     fcitx5-configtool"
echo "   （在附加组件中启用 VoCoType Voice Input；无需添加新输入法）"
echo ""
echo "5. 使用方法："
echo "   - 按住 F9 说话，松开识别（语音输入）"
echo "   - 保持使用你原来的 Rime、拼音、Mozc 或其他 Fcitx 5 输入法"
echo ""
echo "🎤 VoCoType 已增强当前所有 Fcitx 5 输入法！"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

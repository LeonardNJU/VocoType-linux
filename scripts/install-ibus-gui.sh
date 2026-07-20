#!/usr/bin/env bash
# Non-interactive IBus installer used by the graphical settings center.
# All user choices are passed as flags. Privileged operations use pkexec so the
# desktop Polkit agent presents a normal authentication dialog.
set -euo pipefail

NON_INTERACTIVE=false
PRESERVE_CONFIG=false
SKIP_AUDIO=false
INSTALL_SYSTEM_DEPS=false
BOOTSTRAP_UV=false
PYTHON_CHOICE="user"
RIME_MODE="disabled"
RIME_SCHEMA="luna_pinyin"
COMPONENT_MODE="auto"
SLM_PROVIDER="preserve"
AUDIO_DEVICE=""
SAMPLE_RATE="44100"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --non-interactive) NON_INTERACTIVE=true; shift ;;
        --preserve-config) PRESERVE_CONFIG=true; shift ;;
        --skip-audio) SKIP_AUDIO=true; shift ;;
        --install-system-deps) INSTALL_SYSTEM_DEPS=true; shift ;;
        --bootstrap-uv) BOOTSTRAP_UV=true; shift ;;
        --python-choice) PYTHON_CHOICE="${2:?}"; shift 2 ;;
        --rime) RIME_MODE="${2:?}"; shift 2 ;;
        --rime-schema) RIME_SCHEMA="${2:?}"; shift 2 ;;
        --component-mode) COMPONENT_MODE="${2:?}"; shift 2 ;;
        --slm-provider) SLM_PROVIDER="${2:?}"; shift 2 ;;
        --device) AUDIO_DEVICE="${2:?}"; shift 2 ;;
        --sample-rate) SAMPLE_RATE="${2:?}"; shift 2 ;;
        *) echo "未知参数: $1" >&2; exit 2 ;;
    esac
done

if [[ "$NON_INTERACTIVE" != true ]]; then
    echo "该安装器只供图形设置中心非交互调用。" >&2
    exit 2
fi
case "$PYTHON_CHOICE" in user|project|system) ;; *) echo "无效 Python 选项: $PYTHON_CHOICE" >&2; exit 2 ;; esac
case "$RIME_MODE" in enabled|disabled) ;; *) echo "无效 Rime 选项: $RIME_MODE" >&2; exit 2 ;; esac
case "$COMPONENT_MODE" in auto|user|system) ;; *) echo "无效 component 选项: $COMPONENT_MODE" >&2; exit 2 ;; esac
case "$SLM_PROVIDER" in preserve|disabled) ;; *) echo "无效 SLM 选项: $SLM_PROVIDER" >&2; exit 2 ;; esac
if [[ ! "$RIME_SCHEMA" =~ ^[A-Za-z0-9_.-]+$ ]]; then
    echo "无效 Rime schema: $RIME_SCHEMA" >&2
    exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
INSTALL_DIR="$HOME/.local/share/vocotype"
COMPONENT_DIR="$HOME/.local/share/ibus/component"
LIBEXEC_DIR="$HOME/.local/libexec"
SYSTEM_COMPONENT_DIR="/usr/share/ibus/component"
SYSTEM_DEPS_HELPER="$PROJECT_DIR/scripts/install-system-dependencies.sh"
DEFAULT_UV_PYTHON="3.12"

escape_sed_replacement() {
    local value="$1"
    value=${value//\/\\}
    value=${value//&/\&}
    value=${value//|/\|}
    printf '%s' "$value"
}

resolve_python_cmd() {
    local py="$1"
    if [[ "$py" == */* ]]; then
        [[ -x "$py" ]] || return 1
        printf '%s\n' "$py"
    else
        command -v "$py" 2>/dev/null
    fi
}

is_supported_python() {
    "$1" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info[:2] in {(3, 11), (3, 12)} else 1)
PY
}

detect_system_python() {
    local candidate resolved
    for candidate in python3.12 python3.11 python3; do
        resolved=$(resolve_python_cmd "$candidate") || continue
        if is_supported_python "$resolved"; then
            printf '%s\n' "$resolved"
            return 0
        fi
    done
    return 1
}

bootstrap_uv() {
    command -v uv >/dev/null 2>&1 && return 0
    [[ "$BOOTSTRAP_UV" == true ]] || return 1
    echo "正在用户目录安装 uv，以便自动准备 Python 3.12…"
    mkdir -p "$HOME/.local/bin"
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh
    else
        echo "无法自动安装 uv：系统缺少 curl/wget。" >&2
        return 1
    fi
    export PATH="$HOME/.local/bin:$PATH"
    command -v uv >/dev/null 2>&1
}

run_privileged_helper() {
    local action="$1"
    [[ "$INSTALL_SYSTEM_DEPS" == true ]] || return 1
    command -v pkexec >/dev/null 2>&1 || {
        echo "未检测到 pkexec，无法显示管理员授权窗口。" >&2
        return 1
    }
    [[ -r "$SYSTEM_DEPS_HELPER" ]] || {
        echo "系统依赖辅助程序不存在: $SYSTEM_DEPS_HELPER" >&2
        return 1
    }
    echo "AUTH_REQUIRED: 即将弹出管理员授权窗口以安装 $action 系统依赖。"
    pkexec "$(command -v bash)" "$SYSTEM_DEPS_HELPER" "$action"
}

needs_ibus_system_deps() {
    command -v ibus >/dev/null 2>&1 || return 0
    command -v pkg-config >/dev/null 2>&1 || return 0
    pkg-config --exists cairo 2>/dev/null || return 0
    pkg-config --exists gobject-introspection-1.0 2>/dev/null || return 0
    ldconfig -p 2>/dev/null | grep -F libportaudio >/dev/null || return 0
    return 1
}

install_settings_launcher() {
    mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/192x192/apps"
    local python_sed install_sed project_sed
    python_sed=$(escape_sed_replacement "$PYTHON")
    install_sed=$(escape_sed_replacement "$INSTALL_DIR")
    project_sed=$(escape_sed_replacement "$PROJECT_DIR")
    cat > "$HOME/.local/bin/vocotype-settings" <<'LAUNCHER'
#!/bin/bash
PREFERRED_INSTALL_DIR="VOCOTYPE_INSTALL_DIR"
PREFERRED_PYTHON="VOCOTYPE_PYTHON"
export VOCOTYPE_PROJECT_DIR="VOCOTYPE_PROJECT_DIR_VALUE"
run_settings() {
    local install_dir="$1" python_bin="$2"
    shift 2
    [[ -f "$install_dir/settings_center/application.py" ]] || return 1
    [[ "$python_bin" != */* ]] || [[ -x "$python_bin" ]] || return 1
    [[ "$python_bin" == */* ]] || command -v "$python_bin" >/dev/null 2>&1 || return 1
    export PYTHONPATH="$install_dir${PYTHONPATH:+:$PYTHONPATH}"
    exec "$python_bin" -m settings_center.application "$@"
}
run_settings "$PREFERRED_INSTALL_DIR" "$PREFERRED_PYTHON" "$@"
run_settings "$HOME/.local/share/vocotype-fcitx5" "$HOME/.local/share/vocotype-fcitx5/.venv/bin/python" "$@"
run_settings "$HOME/.local/share/vocotype" "$HOME/.local/share/vocotype/.venv/bin/python" "$@"
echo "VoCoType 设置中心运行时不存在，请重新安装或修复。" >&2
exit 1
LAUNCHER
    sed -i "s|VOCOTYPE_PYTHON|$python_sed|g" "$HOME/.local/bin/vocotype-settings"
    sed -i "s|VOCOTYPE_INSTALL_DIR|$install_sed|g" "$HOME/.local/bin/vocotype-settings"
    sed -i "s|VOCOTYPE_PROJECT_DIR_VALUE|$project_sed|g" "$HOME/.local/bin/vocotype-settings"
    chmod +x "$HOME/.local/bin/vocotype-settings"
    sed "s|Exec=vocotype-settings|Exec=$HOME/.local/bin/vocotype-settings|" \
        "$PROJECT_DIR/data/applications/io.github.LeonardNJU.VoCoType.Settings.desktop" > \
        "$HOME/.local/share/applications/io.github.LeonardNJU.VoCoType.Settings.desktop"
    cp "$PROJECT_DIR/site/icon-192.png" "$HOME/.local/share/icons/hicolor/192x192/apps/vocotype.png"
    command -v update-desktop-database >/dev/null 2>&1 && \
        update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
}

echo "=== VoCoType IBus 图形安装后端 ==="
echo "项目目录: $PROJECT_DIR"

NEED_SYSTEM_DEPS=0
needs_ibus_system_deps && NEED_SYSTEM_DEPS=1
if [[ "$RIME_MODE" == enabled ]]; then
    if ! command -v pkg-config >/dev/null 2>&1 ||        ! pkg-config --exists rime 2>/dev/null ||        [[ ! -f /usr/share/rime-data/default.yaml && ! -f /usr/local/share/rime-data/default.yaml ]]; then
        NEED_SYSTEM_DEPS=1
    fi
fi

if [[ "$NEED_SYSTEM_DEPS" == 1 ]]; then
    action="ibus"
    [[ "$RIME_MODE" == enabled ]] && action="ibus-rime"
    echo "检测到缺失的 IBus 系统依赖。"
    run_privileged_helper "$action" || {
        echo "系统依赖安装未完成。请在授权窗口中确认，或检查 Polkit 服务。" >&2
        exit 3
    }
fi
command -v ibus >/dev/null 2>&1 || { echo "安装后仍未检测到 ibus。" >&2; exit 3; }
if [[ "$RIME_MODE" == enabled ]]; then
    command -v pkg-config >/dev/null 2>&1 && pkg-config --exists rime 2>/dev/null || {
        echo "安装后仍未检测到 librime 开发库。" >&2
        exit 3
    }
    [[ -f /usr/share/rime-data/default.yaml || -f /usr/local/share/rime-data/default.yaml ]] || {
        echo "安装后仍未检测到 Rime 共享数据。" >&2
        exit 3
    }
fi

mkdir -p "$INSTALL_DIR" "$COMPONENT_DIR" "$LIBEXEC_DIR"
USE_SYSTEM_PYTHON=0
case "$PYTHON_CHOICE" in
    project) PYTHON="$PROJECT_DIR/.venv/bin/python" ;;
    user) PYTHON="$INSTALL_DIR/.venv/bin/python" ;;
    system) USE_SYSTEM_PYTHON=1; PYTHON="" ;;
esac

if [[ "$USE_SYSTEM_PYTHON" == 1 ]]; then
    PYTHON=$(detect_system_python) || { echo "系统中没有 Python 3.11/3.12。" >&2; exit 4; }
    "$PYTHON" "$PROJECT_DIR/scripts/check-python-runtime.py" || {
        echo "系统 Python 缺少 VoCoType 运行依赖；请改用用户级 Python。" >&2
        exit 4
    }
else
    if [[ ! -x "$PYTHON" ]]; then
        if command -v uv >/dev/null 2>&1 || bootstrap_uv; then
            echo "创建 Python 3.12 用户环境: $(dirname "$PYTHON")/.."
            uv venv --python "$DEFAULT_UV_PYTHON" "$(dirname "$PYTHON")/.."
        else
            PYTHON_CMD=$(detect_system_python) || {
                echo "没有可用的 Python 3.11/3.12，且 uv 自动安装未启用或失败。" >&2
                exit 4
            }
            "$PYTHON_CMD" -m venv "$(dirname "$PYTHON")/.."
        fi
    fi
    echo "安装 Python 依赖…"
    if command -v uv >/dev/null 2>&1; then
        uv pip install --python "$PYTHON" -r "$PROJECT_DIR/requirements.txt"
    else
        "$PYTHON" -m pip install -r "$PROJECT_DIR/requirements.txt"
    fi
fi

if [[ "$RIME_MODE" == enabled ]]; then
    echo "安装 IBus Rime Python 绑定…"
    if command -v uv >/dev/null 2>&1 && [[ "$USE_SYSTEM_PYTHON" != 1 ]]; then
        uv pip install --python "$PYTHON" pyrime
    else
        "$PYTHON" -m pip install pyrime
    fi
fi

IBUS_RUNTIME_CONFIG="$HOME/.config/vocotype/ibus.json"
FCITX_RUNTIME_CONFIG="$HOME/.config/vocotype/fcitx5-backend.json"
mkdir -p "$HOME/.config/vocotype"
if [[ "$PRESERVE_CONFIG" == true ]]; then
    if [[ ! -f "$IBUS_RUNTIME_CONFIG" && -f "$FCITX_RUNTIME_CONFIG" ]]; then
        cp "$FCITX_RUNTIME_CONFIG" "$IBUS_RUNTIME_CONFIG"
        chmod 600 "$IBUS_RUNTIME_CONFIG"
    elif [[ ! -f "$IBUS_RUNTIME_CONFIG" ]]; then
        printf '{}\n' > "$IBUS_RUNTIME_CONFIG"
        chmod 600 "$IBUS_RUNTIME_CONFIG"
    fi
else
    "$PYTHON" - "$IBUS_RUNTIME_CONFIG" "$SLM_PROVIDER" <<'PY'
import json, os, sys
path, provider = sys.argv[1:]
try:
    data = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
except Exception:
    data = {}
slm = data.get("slm") if isinstance(data.get("slm"), dict) else {}
if provider == "disabled":
    slm["enabled"] = False
data["slm"] = slm
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
os.chmod(path, 0o600)
PY
fi

if "$PYTHON" - "$IBUS_RUNTIME_CONFIG" <<'PY' >/dev/null 2>&1
import json, sys
try:
    slm = json.load(open(sys.argv[1], encoding="utf-8")).get("slm", {})
except Exception:
    slm = {}
raise SystemExit(0 if slm.get("enabled") and slm.get("provider") == "local_ephemeral" else 1)
PY
then
    echo "检测到本地 AI 润色配置，安装本地模型依赖…"
    if command -v uv >/dev/null 2>&1 && [[ "$USE_SYSTEM_PYTHON" != 1 ]]; then
        uv pip install --python "$PYTHON" torch transformers sentencepiece socksio
    else
        "$PYTHON" -m pip install torch transformers sentencepiece socksio
    fi
fi

if [[ -n "$AUDIO_DEVICE" ]]; then
    cat > "$HOME/.config/vocotype/audio.conf" <<EOF
[audio]
device_id = $AUDIO_DEVICE
sample_rate = $SAMPLE_RATE
EOF
    chmod 600 "$HOME/.config/vocotype/audio.conf"
elif [[ "$SKIP_AUDIO" != true ]]; then
    echo "图形安装未收到麦克风配置；请先在设置中心选择设备。" >&2
    exit 5
fi

rm -rf "$INSTALL_DIR/app" "$INSTALL_DIR/settings_center" "$INSTALL_DIR/ibus"
cp -r "$PROJECT_DIR/app" "$INSTALL_DIR/"
cp -r "$PROJECT_DIR/settings_center" "$INSTALL_DIR/"
cp -r "$PROJECT_DIR/ibus" "$INSTALL_DIR/"
cp "$PROJECT_DIR/vocotype_version.py" "$INSTALL_DIR/"

TERMS_FILE="$HOME/.config/vocotype/terms.yaml"
LEGACY_TERMS_FILE="$HOME/.config/vocotype/user-dictionary.yaml"
if [[ ! -e "$TERMS_FILE" && ! -e "$LEGACY_TERMS_FILE" ]]; then
    cp "$PROJECT_DIR/data/terms.yaml" "$TERMS_FILE"
    chmod 600 "$TERMS_FILE"
fi

PROJECT_DIR_SED=$(escape_sed_replacement "$PROJECT_DIR")
PYTHON_SED=$(escape_sed_replacement "$PYTHON")
cat > "$LIBEXEC_DIR/ibus-engine-vocotype" <<'LAUNCHER'
#!/bin/bash
VOCOTYPE_HOME="$HOME/.local/share/vocotype"
PROJECT_DIR="VOCOTYPE_PROJECT_DIR"
PYTHON="VOCOTYPE_PYTHON"
export PYTHONPATH="$VOCOTYPE_HOME${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONIOENCODING=UTF-8
export VOCOTYPE_LOG_FILE="$HOME/.local/share/vocotype/ibus.log"
exec "$PYTHON" "$VOCOTYPE_HOME/ibus/main.py" "$@"
LAUNCHER
sed -i "s|VOCOTYPE_PROJECT_DIR|$PROJECT_DIR_SED|g" "$LIBEXEC_DIR/ibus-engine-vocotype"
sed -i "s|VOCOTYPE_PYTHON|$PYTHON_SED|g" "$LIBEXEC_DIR/ibus-engine-vocotype"
chmod +x "$LIBEXEC_DIR/ibus-engine-vocotype"
install_settings_launcher

if [[ "$RIME_MODE" == enabled ]]; then
    mkdir -p "$HOME/.config/vocotype/rime" "$HOME/.local/share/vocotype/rime"
    cat > "$HOME/.config/vocotype/rime/user.yaml" <<EOF
var:
  previously_selected_schema: "$RIME_SCHEMA"
EOF
fi

VOCOTYPE_VERSION=$(PYTHONPATH="$PROJECT_DIR" "$PYTHON" - <<'PY'
from vocotype_version import __version__
print(__version__)
PY
)
TEMP_COMPONENT=$(mktemp "${XDG_RUNTIME_DIR:-/tmp}/vocotype-ibus-component.XXXXXX.xml")
trap 'rm -f "$TEMP_COMPONENT"' EXIT
sed -e "s|VOCOTYPE_EXEC_PATH|$LIBEXEC_DIR/ibus-engine-vocotype|g" \
    -e "s|VOCOTYPE_VERSION|$VOCOTYPE_VERSION|g" \
    "$PROJECT_DIR/data/ibus/vocotype.xml.in" > "$TEMP_COMPONENT"

if [[ "$COMPONENT_MODE" == auto ]]; then
    if [[ "${XDG_CURRENT_DESKTOP:-}" == *GNOME* ]] || [[ -f /etc/debian_version ]] || command -v gnome-shell >/dev/null 2>&1; then
        COMPONENT_MODE=system
    else
        COMPONENT_MODE=user
    fi
fi

if [[ "$COMPONENT_MODE" == system ]]; then
    command -v pkexec >/dev/null 2>&1 || { echo "需要 pkexec 安装系统 IBus component。" >&2; exit 6; }
    INSTALL_BIN=$(command -v install)
    echo "AUTH_REQUIRED: 即将弹出管理员授权窗口以注册 IBus 输入法。"
    pkexec "$INSTALL_BIN" -D -m 0644 "$TEMP_COMPONENT" "$SYSTEM_COMPONENT_DIR/vocotype.xml"
    rm -f "$COMPONENT_DIR/vocotype.xml"
else
    install -D -m 0644 "$TEMP_COMPONENT" "$COMPONENT_DIR/vocotype.xml"
fi

if command -v ibus >/dev/null 2>&1 && [[ -n "${DBUS_SESSION_BUS_ADDRESS:-}${XDG_RUNTIME_DIR:-}" ]]; then
    ibus restart >/dev/null 2>&1 || true
fi

echo "✅ IBus 安装/修复完成。"
echo "请在桌面输入源设置中添加“VoCoType Voice Input”。"

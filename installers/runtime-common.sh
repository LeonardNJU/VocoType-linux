#!/usr/bin/env bash
# Shared, side-effect-free installer helpers used by both integrations.

ensure_desktop_user_environment() {
    local uid runtime_dir key value
    uid=$(id -u)
    runtime_dir="${XDG_RUNTIME_DIR:-/run/user/$uid}"
    if [ -d "$runtime_dir" ]; then
        export XDG_RUNTIME_DIR="$runtime_dir"
    fi
    if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ] && [ -S "$runtime_dir/bus" ]; then
        export DBUS_SESSION_BUS_ADDRESS="unix:path=$runtime_dir/bus"
    fi

    # systemd user manager normally preserves the desktop display variables,
    # even when this installer was launched from SSH or an automation shell.
    if command -v systemctl >/dev/null 2>&1 && [ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]; then
        while IFS='=' read -r key value; do
            case "$key" in
                DISPLAY|WAYLAND_DISPLAY|XAUTHORITY|XDG_CURRENT_DESKTOP|DESKTOP_SESSION)
                    [ -n "$value" ] && export "$key=$value"
                    ;;
            esac
        done < <(systemctl --user show-environment 2>/dev/null || true)
    fi
}

get_python_version() {
    "$1" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null
}

emit_install_progress() {
    local percent="$1"
    shift
    printf 'VOCOTYPE_PROGRESS:%s:%s\n' "$percent" "$*"
}

download_and_verify_asr_models() {
    local python_bin="$1"
    local runtime_root="$2"
    echo ""
    echo "下载并校验 VoCoType 必需模型（ASR、VAD、标点）..."
    echo "网络策略：先使用当前代理；失败时自动改用无代理直连重试。"
    if ! PYTHONPATH="$runtime_root${PYTHONPATH:+:$PYTHONPATH}" \
        "$python_bin" -m app.download_models; then
        echo "错误: 必需模型未全部下载并通过完整性校验，安装不能继续。" >&2
        return 1
    fi
    echo "✓ ASR、VAD、标点模型均完整可用"
}

write_slm_config_json() {
    local config_file="$1"
    local python_bin="$2"
    local enabled="$3"
    local endpoint="$4"
    local model="$5"
    local timeout_ms="$6"
    local min_chars="$7"
    local max_tokens="$8"
    local enable_thinking="$9"
    local api_key="${10}"

    "$python_bin" - "$config_file" "$enabled" "$endpoint" "$model" "$timeout_ms" "$min_chars" "$max_tokens" "$enable_thinking" "$api_key" << 'PY'
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
endpoint = sys.argv[3]
model = sys.argv[4]
timeout_ms = int(sys.argv[5])
min_chars = int(sys.argv[6])
max_tokens = int(sys.argv[7])
enable_thinking = bool(int(sys.argv[8]))
api_key = sys.argv[9]

cfg = load_json(target)
slm = cfg.get("slm", {})
if not isinstance(slm, dict):
    slm = {}
for obsolete in (
    "provider",
    "local_model",
    "local_python",
    "local_device",
    "local_dtype",
    "warmup_timeout_ms",
    "keepalive_ms",
    "ready_wait_ms",
):
    slm.pop(obsolete, None)
slm.update(
    {
        "enabled": enabled,
        "endpoint": endpoint,
        "model": model,
        "timeout_ms": timeout_ms,
        "stream_idle_timeout_ms": timeout_ms,
        "remote_stream": True,
        "transport_timeout_ms": int(slm.get("transport_timeout_ms", 0) or 0),
        "remote_max_tokens": int(slm.get("remote_max_tokens", 0) or 0),
        "extra_headers": slm.get("extra_headers", {}),
        "extra_body": slm.get("extra_body", {}),
        "min_chars": min_chars,
        "max_tokens": max_tokens,
        "enable_thinking": enable_thinking,
        "api_key": api_key,
    }
)
cfg["slm"] = slm

os.makedirs(os.path.dirname(target), exist_ok=True)
with open(target, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
    f.write("\n")
os.chmod(target, 0o600)
PY
}


find_system_streaming_worker() {
    local candidate
    for candidate in \
        /usr/libexec/vocotype-streaming-worker \
        /usr/lib/vocotype/vocotype-streaming-worker \
        /usr/lib64/vocotype/vocotype-streaming-worker \
        /usr/lib/*/vocotype/vocotype-streaming-worker; do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

runtime_wheelhouse_dir() {
    local project_dir="$1"
    if [ -d "${VOCOTYPE_WHEELHOUSE_DIR:-}" ]; then
        printf '%s\n' "$VOCOTYPE_WHEELHOUSE_DIR"
    elif [ -d "$project_dir/wheelhouse" ]; then
        printf '%s\n' "$project_dir/wheelhouse"
    elif [ -d "$project_dir/vendor/wheelhouse" ]; then
        printf '%s\n' "$project_dir/vendor/wheelhouse"
    else
        return 1
    fi
}

install_runtime_requirements() {
    local python_bin="$1"
    local project_dir="$2"
    local wheelhouse=""
    wheelhouse=$(runtime_wheelhouse_dir "$project_dir" 2>/dev/null || true)
    if [ -n "$wheelhouse" ]; then
        if command -v uv >/dev/null 2>&1; then
            uv pip install --python "$python_bin" \
                --no-index --find-links "$wheelhouse" \
                --only-binary :all: \
                -r "$project_dir/requirements.txt"
        else
            "$python_bin" -m pip install \
                --no-index --find-links "$wheelhouse" \
                --only-binary=:all: \
                -r "$project_dir/requirements.txt"
        fi
        return
    fi
    if [ -f "$project_dir/.system-package" ] || [ -f /usr/share/vocotype/.system-package ]; then
        echo "错误: Release 安装缺少预构建 Python wheelhouse，拒绝在本机编译依赖。" >&2
        return 1
    fi
    if command -v uv >/dev/null 2>&1; then
        uv pip install --python "$python_bin" \
            --only-binary :all: \
            -r "$project_dir/requirements.txt"
    else
        "$python_bin" -m pip install \
            --only-binary=:all: \
            -r "$project_dir/requirements.txt"
    fi
}

install_binary_packages() {
    local python_bin="$1"
    local project_dir="$2"
    shift 2
    local wheelhouse=""
    local -a links=()
    wheelhouse=$(runtime_wheelhouse_dir "$project_dir" 2>/dev/null || true)
    if [ -n "$wheelhouse" ]; then
        links=(--find-links "$wheelhouse")
    fi
    if command -v uv >/dev/null 2>&1; then
        uv pip install --python "$python_bin" \
            --only-binary :all: \
            "${links[@]}" "$@"
    else
        "$python_bin" -m pip install \
            --only-binary=:all: \
            "${links[@]}" "$@"
    fi
}

install_native_streaming_bundle() {
    local project_dir="$1"
    local source_dir="${VOCOTYPE_STREAMING_BUNDLE_DIR:-$project_dir/native/streaming_worker/build/bundle}"
    local target_dir="${VOCOTYPE_STREAMING_INSTALL_DIR:-$HOME/.local/lib/vocotype-streaming}"
    local system_worker=""

    system_worker=$(find_system_streaming_worker 2>/dev/null || true)
    if [ -n "$system_worker" ]; then
        echo "✓ 使用 Release 包内预编译 native streaming runtime: $system_worker"
        return 0
    fi

    case "$target_dir" in
        ""|/|"$HOME")
            echo "错误: 不安全的 native streaming 安装目录: $target_dir" >&2
            return 1
            ;;
    esac

    if [ ! -x "$source_dir/bin/vocotype-streaming-worker" ] || [ ! -d "$source_dir/lib" ]; then
        if [ -f "$project_dir/.system-package" ] || [ -f /usr/share/vocotype/.system-package ]; then
            echo "错误: Release 包缺少 native streaming runtime，安装不完整。" >&2
            return 1
        fi
        echo "提示: 源码树未构建 native streaming bundle；实时识别预览保持不可用。"
        return 0
    fi

    rm -rf "$target_dir"
    mkdir -p "$target_dir"
    cp -a "$source_dir/." "$target_dir/"
    chmod +x "$target_dir/bin/vocotype-streaming-worker"
    echo "✓ 已安装可按需加载的 native streaming runtime"
}

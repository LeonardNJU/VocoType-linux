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

resolve_python_cmd() {
    local py="$1"

    if [[ "$py" == "~/"* ]]; then
        py="$HOME/${py#~/}"
    fi

    if [[ "$py" == */* ]]; then
        [ -x "$py" ] || return 1
        printf '%s\n' "$py"
        return 0
    fi

    command -v "$py" 2>/dev/null || return 1
}

is_supported_python() {
    local py="$1"
    local py_version major minor

    py_version=$(get_python_version "$py") || return 1
    major=${py_version%%.*}
    minor=${py_version#*.}
    [ "$major" -eq 3 ]         && [ "$minor" -ge "${PYTHON_MIN_MINOR:-11}" ]         && [ "$minor" -le "${PYTHON_MAX_MINOR:-12}" ]
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

escape_sed_replacement() {
    local value="$1"
    value=${value//\\/\\\\}
    value=${value//&/\\&}
    value=${value//|/\\|}
    printf '%s' "$value"
}

refresh_user_desktop_database() {
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
    fi
}

install_settings_launcher() {
    local project_dir="$1"
    local install_dir="$2"
    local preferred_python="$3"
    local system_prefix="${VOCOTYPE_SYSTEM_PREFIX:-/usr}"
    local user_launcher="$HOME/.local/bin/vocotype-settings"
    local user_desktop="$HOME/.local/share/applications/io.github.LeonardNJU.VoCoType.Settings.desktop"
    local system_launcher="$system_prefix/bin/vocotype-settings"

    mkdir -p \
        "$HOME/.local/bin" \
        "$HOME/.local/share/applications" \
        "$HOME/.local/share/icons/hicolor/192x192/apps"

    # Native packages own the canonical launcher and desktop file. Remove any
    # user-level launcher left by an older source installer, because ~/.local/bin
    # normally shadows /usr/bin and would otherwise force the private ASR Python
    # to start the GTK application without distro PyGObject.
    if { [ -f "$project_dir/.system-package" ] || \
         [ -f "$system_prefix/share/vocotype/.system-package" ]; } && \
       [ -x "$system_launcher" ]; then
        rm -f "$user_launcher" "$user_desktop"
        refresh_user_desktop_database
        echo "✓ 使用原生软件包设置中心入口: $system_launcher"
        return 0
    fi

    local python_sed install_sed project_sed
    python_sed=$(escape_sed_replacement "$preferred_python")
    install_sed=$(escape_sed_replacement "$install_dir")
    project_sed=$(escape_sed_replacement "$project_dir")
    cat > "$user_launcher" <<'LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail
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
    if ! PYTHONPATH="$install_dir${PYTHONPATH:+:$PYTHONPATH}" \
        "$python_bin" -c 'import settings_center.application' >/dev/null 2>&1; then
        return 1
    fi
    export PYTHONPATH="$install_dir${PYTHONPATH:+:$PYTHONPATH}"
    exec "$python_bin" -m settings_center.application "$@"
}

# GTK belongs to the distro Python. Probe it first, then fall back to a private
# runtime only when that runtime can import the complete settings application.
run_settings "$PREFERRED_INSTALL_DIR" python3 "$@"
run_settings "$PREFERRED_INSTALL_DIR" "$PREFERRED_PYTHON" "$@"
run_settings "$HOME/.local/share/vocotype-fcitx5" python3 "$@"
run_settings "$HOME/.local/share/vocotype-fcitx5" "$HOME/.local/share/vocotype-fcitx5/.venv/bin/python" "$@"
run_settings "$HOME/.local/share/vocotype" python3 "$@"
run_settings "$HOME/.local/share/vocotype" "$HOME/.local/share/vocotype/.venv/bin/python" "$@"

echo "VoCoType Settings could not find a Python interpreter able to import GTK 3, PyGObject, PyYAML, NumPy, and the settings modules." >&2
exit 78
LAUNCHER
    sed -i "s|VOCOTYPE_PYTHON|$python_sed|g" "$user_launcher"
    sed -i "s|VOCOTYPE_INSTALL_DIR|$install_sed|g" "$user_launcher"
    sed -i "s|VOCOTYPE_PROJECT_DIR_VALUE|$project_sed|g" "$user_launcher"
    chmod +x "$user_launcher"
    sed "s|Exec=vocotype-settings|Exec=$user_launcher|" \
        "$project_dir/data/applications/io.github.LeonardNJU.VoCoType.Settings.desktop" > \
        "$user_desktop"
    cp "$project_dir/site/icon-192.png" \
       "$HOME/.local/share/icons/hicolor/192x192/apps/vocotype.png"
    refresh_user_desktop_database
    echo "✓ 设置中心已安装，可运行: vocotype-settings"
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
    local system_prefix="${VOCOTYPE_SYSTEM_PREFIX:-/usr}"
    for candidate in \
        "$system_prefix/libexec/vocotype-streaming-worker" \
        "$system_prefix/lib/vocotype/vocotype-streaming-worker" \
        "$system_prefix/lib64/vocotype/vocotype-streaming-worker" \
        "$system_prefix"/lib/*/vocotype/vocotype-streaming-worker; do
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

runtime_wheelhouse_flavor() {
    local project_dir="$1"
    local marker="$project_dir/.system-package"
    local flavor=""
    if [ -f "$marker" ]; then
        flavor=$(sed -n 's/^flavor=//p' "$marker" | head -n 1)
    fi
    case "$flavor" in
        universal|ibus|fcitx5) printf '%s\n' "$flavor" ;;
        *) printf '%s\n' "${VOCOTYPE_WHEELHOUSE_FLAVOR:-fcitx5}" ;;
    esac
}

runtime_requirements_file() {
    local project_dir="$1"
    if [ -f "$project_dir/runtime-requirements.txt" ]; then
        printf '%s\n' "$project_dir/runtime-requirements.txt"
    else
        printf '%s\n' "$project_dir/requirements.txt"
    fi
}

verify_runtime_wheelhouse() {
    local project_dir="$1"
    local wheelhouse="$2"
    local audit="$project_dir/packaging/tools/audit-wheelhouse.py"
    local flavor=""
    flavor=$(runtime_wheelhouse_flavor "$project_dir")
    if [ -f "$project_dir/.wheelhouse.sha256" ]; then
        (cd "$project_dir" && sha256sum -c .wheelhouse.sha256)
    fi
    if [ -f "$audit" ]; then
        python3 "$audit" "$wheelhouse" --flavor "$flavor"
    fi
}

install_runtime_requirements() {
    local python_bin="$1"
    local project_dir="$2"
    local wheelhouse=""
    local requirements=""
    requirements=$(runtime_requirements_file "$project_dir")
    wheelhouse=$(runtime_wheelhouse_dir "$project_dir" 2>/dev/null || true)
    if [ -n "$wheelhouse" ]; then
        verify_runtime_wheelhouse "$project_dir" "$wheelhouse"
        if command -v uv >/dev/null 2>&1; then
            uv pip install --python "$python_bin" \
                --no-index --find-links "$wheelhouse" \
                --only-binary :all: \
                -r "$requirements"
        else
            "$python_bin" -m pip install \
                --no-index --find-links "$wheelhouse" \
                --only-binary=:all: \
                -r "$requirements"
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
            -r "$requirements"
    else
        "$python_bin" -m pip install \
            --only-binary=:all: \
            -r "$requirements"
    fi
}

install_binary_packages() {
    local python_bin="$1"
    local project_dir="$2"
    shift 2
    local wheelhouse=""
    local -a repository_args=()
    wheelhouse=$(runtime_wheelhouse_dir "$project_dir" 2>/dev/null || true)
    if [ -n "$wheelhouse" ]; then
        verify_runtime_wheelhouse "$project_dir" "$wheelhouse"
        repository_args=(--no-index --find-links "$wheelhouse")
    fi
    if command -v uv >/dev/null 2>&1; then
        uv pip install --python "$python_bin" \
            --only-binary :all: \
            "${repository_args[@]}" "$@"
    else
        "$python_bin" -m pip install \
            --only-binary=:all: \
            "${repository_args[@]}" "$@"
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

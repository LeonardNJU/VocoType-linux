#!/usr/bin/env bash
set -euo pipefail

FRAMEWORK=universal
NON_INTERACTIVE=false
DOWNLOAD_MODELS=false
INSTALL_SYSTEM_DEPS=false
PRESERVE_CONFIG=true
DEVICE=""
SAMPLE_RATE=""

usage() {
  cat <<'HELP'
Usage: install-native-user.sh [options]
  --framework fcitx5|ibus|universal
  --non-interactive
  --download-models
  --install-system-deps
  --device ID
  --sample-rate HZ

The native installer preserves ~/.config/vocotype and the ModelScope cache.
HELP
}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --framework) FRAMEWORK="${2:?missing framework}"; shift 2 ;;
    --non-interactive) NON_INTERACTIVE=true; shift ;;
    --download-models) DOWNLOAD_MODELS=true; shift ;;
    --install-system-deps) INSTALL_SYSTEM_DEPS=true; shift ;;
    --device) DEVICE="${2:?missing device}"; shift 2 ;;
    --sample-rate) SAMPLE_RATE="${2:?missing rate}"; shift 2 ;;
    --preserve-config|--skip-audio) shift ;;
    # Legacy Python flags are accepted and ignored during upgrades.
    --bootstrap-uv) shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done
case "$FRAMEWORK" in fcitx5|ibus|universal) ;; *) echo "invalid framework" >&2; exit 2 ;; esac

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
SYSTEM_ROOT=${VOCOTYPE_SYSTEM_ROOT:-/usr/share/vocotype}
PACKAGE_MARKER="$SYSTEM_ROOT/.system-package"
SOURCE_MODE=false
if [[ -f "$PROJECT_DIR/native/desktop/CMakeLists.txt" &&
      -f "$PROJECT_DIR/native/streaming_worker/build.sh" ]]; then
  SOURCE_MODE=true
elif [[ ! -f "$PACKAGE_MARKER" ]]; then
  echo "Cannot locate a source tree or an installed native package." >&2
  exit 2
fi

NATIVE_HOME="$HOME/.local/lib/vocotype-native"
NATIVE_BIN="$NATIVE_HOME/bin"
STREAMING_HOME="$HOME/.local/lib/vocotype-streaming"
USER_BIN="$HOME/.local/bin"
USER_SERVICE_DIR="$HOME/.config/systemd/user"
USER_COMPONENT_DIR="$HOME/.local/share/ibus/component"
USER_LIBEXEC="$HOME/.local/libexec"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/vocotype"
mkdir -p "$CONFIG_DIR" "$USER_BIN" "$NATIVE_BIN"

if [[ -z "${XDG_RUNTIME_DIR:-}" && -d "/run/user/$(id -u)" ]]; then
  export XDG_RUNTIME_DIR="/run/user/$(id -u)"
fi
if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" && -S "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/bus" ]]; then
  export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/bus"
fi

log() { printf '%s\n' "$*"; }
manager_cmd() { printf 'system%s' ctl; }
manager_user() { local manager; manager=$(manager_cmd); "$manager" --user "$@"; }
manager_available() {
  command -v "$(manager_cmd)" >/dev/null 2>&1 &&
    manager_user show-environment >/dev/null 2>&1
}

restart_fcitx_desktop() {
  local unit='app-org.fcitx.Fcitx5@autostart.service'
  if manager_available; then
    local load_state manager_environment
    load_state=$(manager_user show "$unit" -p LoadState --value 2>/dev/null || true)
    manager_environment=$(manager_user show-environment 2>/dev/null || true)
    if [[ "$load_state" == loaded ]] &&
       grep -Eq '^(DISPLAY|WAYLAND_DISPLAY)=' <<<"$manager_environment"; then
      manager_user restart "$unit"
      return 0
    fi
  fi
  if command -v fcitx5 >/dev/null 2>&1 &&
     [[ -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ]]; then
    env -u FCITX_ADDON_DIRS fcitx5 -r -d >/dev/null 2>&1 || true
    return 0
  fi
  log "Fcitx 5 module installed; desktop restart deferred because no graphical session is available."
}

install_deps() {
  [[ "$INSTALL_SYSTEM_DEPS" == true ]] || return 0
  local flavor=$FRAMEWORK
  [[ "$flavor" == fcitx5 ]] && flavor=fcitx5-source
  [[ "$flavor" == ibus ]] && flavor=ibus-rime
  if [[ -x "$PROJECT_DIR/installers/install-system-dependencies.sh" ]]; then
    if command -v pkexec >/dev/null 2>&1; then
      pkexec bash "$PROJECT_DIR/installers/install-system-dependencies.sh" "$flavor"
    else
      sudo bash "$PROJECT_DIR/installers/install-system-dependencies.sh" "$flavor"
    fi
  fi
}

resolve_system_binary() {
  local name=$1 candidate
  for candidate in \
    "/usr/bin/$name" \
    "/usr/libexec/$name" \
    "/usr/lib/vocotype/$name" \
    "/usr/lib64/vocotype/$name"; do
    [[ -x "$candidate" ]] && { printf '%s\n' "$candidate"; return 0; }
  done
  return 1
}

settings_binary() {
  if [[ "$SOURCE_MODE" == true && -x "$NATIVE_BIN/vocotype-settings" ]]; then
    printf '%s\n' "$NATIVE_BIN/vocotype-settings"
    return 0
  fi
  resolve_system_binary vocotype-settings
}

migrate_legacy_fcitx_profile() {
  local settings
  settings=$(settings_binary) || {
    echo "native settings helper is missing; cannot migrate the Fcitx profile" >&2
    return 1
  }
  "$settings" --repair-fcitx-profile
}

verify_fcitx_addon_enabled() {
  local settings state rc attempt enabled_once=false
  settings=$(settings_binary) || {
    echo "native settings helper is missing; cannot verify the Fcitx addon" >&2
    return 1
  }
  if [[ -z "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]]; then
    log "Fcitx addon verification deferred because no graphical session is available."
    return 0
  fi
  if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]]; then
    log "Fcitx addon verification deferred because no user D-Bus is available."
    return 0
  fi

  for attempt in $(seq 1 80); do
    if state=$("$settings" --fcitx-addon-state vocotype 2>&1); then
      log "✓ Fcitx discovered and enabled the VoCoType addon."
      return 0
    else
      rc=$?
    fi
    case "$rc" in
      10)
        if [[ "$enabled_once" == false ]]; then
          log "VoCoType addon is installed but disabled; enabling it through Fcitx D-Bus…"
          "$settings" --enable-fcitx-addon vocotype
          enabled_once=true
          restart_fcitx_desktop
        fi
        ;;
      11|12) ;;
      *)
        echo "unexpected Fcitx addon probe failure (exit=$rc): $state" >&2
        return 1
        ;;
    esac
    sleep 0.1
  done
  echo "Fcitx did not load an enabled VoCoType addon after repair: $state" >&2
  return 1
}

build_source_runtime() {
  install_deps
  if [[ ! -x "$PROJECT_DIR/native/streaming_worker/build/bundle/bin/vocotype-core" ]]; then
    log "Building pinned FunASR/ONNX native runtime…"
    "$PROJECT_DIR/packaging/tools/build-native-streaming-release.sh" \
      "$PROJECT_DIR/dist/native" >/tmp/vocotype-native-release-path.txt
  fi
  rm -rf "$STREAMING_HOME"
  mkdir -p "$STREAMING_HOME"
  cp -a "$PROJECT_DIR/native/streaming_worker/build/bundle/." "$STREAMING_HOME/"
  (
    cd "$STREAMING_HOME"
    find bin lib -maxdepth 1 \( -type f -o -type l \) -print0 \
      | sort -z | xargs -0 sha256sum > .native-payload.sha256
  )

  local desktop_build="$PROJECT_DIR/build/native-user-$FRAMEWORK"
  local build_ibus=OFF build_rime=OFF
  if [[ "$FRAMEWORK" == ibus || "$FRAMEWORK" == universal ]]; then
    build_ibus=ON
    build_rime=ON
  fi
  local extra_pkgconfig=""
  if [[ -f "$PROJECT_DIR/build/ibus-sdk/pkgconfig/ibus-1.0.pc" ]]; then
    extra_pkgconfig="$PROJECT_DIR/build/ibus-sdk/pkgconfig"
  fi
  log "Building native desktop components…"
  PKG_CONFIG_PATH="$extra_pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}" \
    cmake -S "$PROJECT_DIR/native/desktop" -B "$desktop_build" \
      -DCMAKE_BUILD_TYPE=Release \
      -DVOCOTYPE_BUILD_SETTINGS=ON \
      -DVOCOTYPE_BUILD_IBUS="$build_ibus" \
      -DVOCOTYPE_BUILD_RIME="$build_rime" \
      -DBUILD_TESTING=OFF
  PKG_CONFIG_PATH="$extra_pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}" \
    cmake --build "$desktop_build" --parallel "${JOBS:-2}"
  rm -rf "$NATIVE_BIN"
  mkdir -p "$NATIVE_BIN"
  install -m755 "$desktop_build/vocotype-audio-recorder" "$NATIVE_BIN/"
  install -m755 "$desktop_build/vocotype-model-manager" "$NATIVE_BIN/"
  install -m755 "$desktop_build/vocotype-settings" "$NATIVE_BIN/"
  if [[ "$build_ibus" == ON ]]; then
    install -m755 "$desktop_build/vocotype-ibus-engine" "$NATIVE_BIN/"
  fi
}

write_default_config() {
  local path="$CONFIG_DIR/fcitx5-backend.json"
  [[ -f "$path" ]] && return 0
  umask 077
  cat > "$path" <<'JSON'
{
  "audio": {"sample_rate": 16000, "block_ms": 20, "device": null, "min_recording_ms": 1000},
  "asr": {"native_enabled": true, "use_vad": false, "use_punc": true, "itn": true},
  "asr_streaming": {"enabled": true, "chunk_size": [5, 10, 5]},
  "normalization": {"enabled": true, "compact_dates": true, "compact_times": true, "compact_distances": true, "currency_symbols": true},
  "slm": {"enabled": false, "endpoint": "http://127.0.0.1:18080/v1/chat/completions", "model": "Qwen/Qwen3.5-0.8B", "timeout_ms": 20000, "remote_stream": true, "min_chars": 8, "max_tokens": 128, "enable_thinking": false, "edit_enabled": true, "edit_max_tokens": 1024}
}
JSON
}

write_terms_template() {
  local path="$CONFIG_DIR/terms.yaml"
  [[ -f "$path" ]] && return 0
  umask 077
  cat > "$path" <<'YAML'
# Unified terminology, hotword, and protection list.
terms:
  - canonical: VoCoType
    aliases: [沃口泰普]
    hotword: true
    protect: true

protect:
  - 三体问题
YAML
}

write_audio_override() {
  [[ -n "$DEVICE" || -n "$SAMPLE_RATE" ]] || return 0
  local rate=${SAMPLE_RATE:-16000}
  umask 077
  cat > "$CONFIG_DIR/audio.conf" <<EOF_AUDIO
[audio]
device_id = $DEVICE
sample_rate = $rate
EOF_AUDIO
}

install_user_launchers() {
  cat > "$USER_BIN/vocotype-fcitx5-backend" <<'LAUNCH'
#!/usr/bin/env bash
set -euo pipefail
for core in "$HOME/.local/lib/vocotype-streaming/bin/vocotype-core" /usr/libexec/vocotype-core /usr/lib/vocotype/vocotype-core /usr/lib64/vocotype/vocotype-core; do
  [[ -x "$core" ]] || continue
  exec "$core" --enable-final-asr "$@"
done
echo "VoCoType native core is not installed" >&2
exit 78
LAUNCH
  cat > "$USER_BIN/vocotype-fcitx5-recorder" <<'LAUNCH'
#!/usr/bin/env bash
set -euo pipefail
for recorder in "$HOME/.local/lib/vocotype-native/bin/vocotype-audio-recorder" /usr/libexec/vocotype-audio-recorder /usr/lib/vocotype/vocotype-audio-recorder /usr/lib64/vocotype/vocotype-audio-recorder; do
  [[ -x "$recorder" ]] || continue
  exec "$recorder" "$@"
done
echo "VoCoType native audio recorder is not installed" >&2
exit 78
LAUNCH
  cat > "$USER_BIN/vocotype-settings" <<'LAUNCH'
#!/usr/bin/env bash
set -euo pipefail
for settings in "$HOME/.local/lib/vocotype-native/bin/vocotype-settings" /usr/bin/vocotype-settings; do
  [[ -x "$settings" ]] || continue
  [[ "$settings" == "$0" ]] && continue
  exec "$settings" "$@"
done
echo "VoCoType native settings center is not installed" >&2
exit 78
LAUNCH
  chmod 0755 "$USER_BIN/vocotype-fcitx5-backend" \
    "$USER_BIN/vocotype-fcitx5-recorder" "$USER_BIN/vocotype-settings"
}

install_fcitx() {
  mkdir -p "$USER_SERVICE_DIR"
  cat > "$USER_SERVICE_DIR/vocotype-fcitx5-backend.service" <<EOF_SERVICE
[Unit]
Description=VoCoType Native Fcitx 5 Backend
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=$USER_BIN/vocotype-fcitx5-backend
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF_SERVICE
  if [[ "$SOURCE_MODE" == true ]]; then
    local build="$PROJECT_DIR/build/fcitx-native-user"
    cmake -S "$PROJECT_DIR/fcitx5/module" -B "$build" -DCMAKE_BUILD_TYPE=Release
    cmake --build "$build" --parallel "${JOBS:-2}"
    mkdir -p "$HOME/.local/lib/fcitx5" "$HOME/.local/share/fcitx5/addon"
    install -m755 "$build/vocotype.so" "$HOME/.local/lib/fcitx5/vocotype.so"
    install -m644 "$PROJECT_DIR/fcitx5/data/vocotype.conf" \
      "$HOME/.local/share/fcitx5/addon/vocotype.conf"
    sed -i "s|^Library=.*|Library=$HOME/.local/lib/fcitx5/vocotype|" \
      "$HOME/.local/share/fcitx5/addon/vocotype.conf"
    echo "✓ Installed the Fcitx module in the user prefix; no root privilege was required."
  fi
  migrate_legacy_fcitx_profile
  if manager_available; then
    manager_user daemon-reload
    manager_user enable vocotype-fcitx5-backend.service >/dev/null
    manager_user restart vocotype-fcitx5-backend.service
  fi
  restart_fcitx_desktop
  verify_fcitx_addon_enabled
}

install_ibus() {
  local engine
  if [[ "$SOURCE_MODE" == true ]]; then engine="$NATIVE_BIN/vocotype-ibus-engine"
  else engine=$(resolve_system_binary vocotype-ibus-engine); fi
  [[ -x "$engine" ]] || { echo "native IBus engine is missing" >&2; exit 1; }
  mkdir -p "$USER_COMPONENT_DIR" "$USER_LIBEXEC"
  install -m755 "$engine" "$USER_LIBEXEC/ibus-engine-vocotype"
  local version=3
  [[ -f "$PROJECT_DIR/VERSION" ]] && version=$(tr -d '[:space:]' < "$PROJECT_DIR/VERSION")
  cat > "$USER_COMPONENT_DIR/vocotype.xml" <<EOF_COMPONENT
<?xml version="1.0" encoding="utf-8"?>
<component><name>org.vocotype.IBus.VoCoType</name><description>VoCoType Voice Input Method</description><exec>$USER_LIBEXEC/ibus-engine-vocotype --ibus</exec><version>$version</version><author>VoCoType</author><license>GPL</license><homepage>https://github.com/LeonardNJU/VocoType-linux</homepage><textdomain>vocotype</textdomain><engines><engine><name>vocotype</name><language>zh</language><license>GPL</license><author>VoCoType</author><layout>default</layout><longname>VoCoType Voice Input</longname><description>Push-to-Talk Voice Input (F9)</description><rank>50</rank><symbol>🎤</symbol></engine></engines></component>
EOF_COMPONENT
  "$engine" --deploy-rime >/dev/null 2>&1 || true
  command -v ibus >/dev/null 2>&1 && ibus restart >/dev/null 2>&1 || true
}

verify_models() {
  local manager
  if [[ "$SOURCE_MODE" == true ]]; then manager="$NATIVE_BIN/vocotype-model-manager"
  else manager=$(resolve_system_binary vocotype-model-manager); fi
  [[ -x "$manager" ]] || { echo "native model manager is missing" >&2; exit 1; }
  if ! "$manager" --check --all >/tmp/vocotype-model-check.json; then
    if [[ "$DOWNLOAD_MODELS" == true ]]; then
      "$manager" --download --all
    else
      echo "ASR models are missing or invalid. Re-run with --download-models." >&2
      return 1
    fi
  fi
}

cleanup_legacy_runtime() {
  # Keep user configuration and models. Only remove the integration that was
  # successfully migrated in this invocation.
  case "$FRAMEWORK" in
    fcitx5|universal)
      rm -rf "$HOME/.local/share/vocotype-fcitx5/.venv" \
             "$HOME/.local/share/vocotype-fcitx5/app" \
             "$HOME/.local/share/vocotype-fcitx5/backend" \
             "$HOME/.local/share/vocotype-fcitx5/settings_center"
      ;;
  esac
  case "$FRAMEWORK" in
    ibus|universal)
      rm -rf "$HOME/.local/share/vocotype/.venv" \
             "$HOME/.local/share/vocotype/app" \
             "$HOME/.local/share/vocotype/ibus" \
             "$HOME/.local/share/vocotype/settings_center"
      ;;
  esac
}

log "=== VoCoType native-only installation ($FRAMEWORK) ==="
if [[ "$SOURCE_MODE" == true ]]; then build_source_runtime; fi
write_default_config
write_terms_template
write_audio_override
install_user_launchers
verify_models
case "$FRAMEWORK" in
  fcitx5) install_fcitx ;;
  ibus) install_ibus ;;
  universal) install_fcitx; install_ibus ;;
esac
cleanup_legacy_runtime
log "✓ Native-only runtime installed. Configuration and model caches were preserved."

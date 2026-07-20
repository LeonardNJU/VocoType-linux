#!/usr/bin/env bash
# Launch the VoCoType graphical setup/settings center from a source checkout.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"

find_gui_python() {
    local candidate
    for candidate in \
        "$PROJECT_DIR/.venv/bin/python" \
        "$HOME/.local/share/vocotype-fcitx5/.venv/bin/python" \
        "$HOME/.local/share/vocotype/.venv/bin/python" \
        python3 python3.12 python3.11; do
        if [[ "$candidate" == */* ]]; then
            [ -x "$candidate" ] || continue
        else
            command -v "$candidate" >/dev/null 2>&1 || continue
        fi
        if "$candidate" - <<'PY' >/dev/null 2>&1
import gi
gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: F401
PY
        then
            if [[ "$candidate" == */* ]]; then
                printf '%s\n' "$candidate"
            else
                command -v "$candidate"
            fi
            return 0
        fi
    done
    return 1
}

PYTHON="$(find_gui_python || true)"
if [ -z "$PYTHON" ]; then
    cat >&2 <<'EOF'
无法启动图形设置中心：系统 Python 缺少 GTK 3 的 PyGObject 绑定。

请先安装：
  Fedora:        sudo dnf install python3-gobject gtk3
  Debian/Ubuntu: sudo apt install python3-gi gir1.2-gtk-3.0
  Arch:          sudo pacman -S python-gobject gtk3
EOF
    exit 1
fi

export VOCOTYPE_PROJECT_DIR="$PROJECT_DIR"
export PYTHONPATH="$PROJECT_DIR${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" -m settings_center.application "$@"
